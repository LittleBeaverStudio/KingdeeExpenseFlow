#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报销单「明细 ↔ 发票」对齐 + 附件上传（纯金蝶 WebAPI）

配套 recvin_link.py（那个负责「收票信息」）。本模块负责另外三件事：

  1) attach   把行程单/佐证文件传到单据【附件】（不是收票信息！）
  2) set-detail 把明细金额/费用项目改成与发票一致（应 ≤ 发票价税合计）
  3) linkage  写「明细 ↔ 发票分录」联动关系（= UI 的「合并生成费用明细」底层数据）

⚠️ 三个实测出来的硬坑（都踩过）：

  A. `AttachmentUpLoad` / `UploadFile` 的 `IsLast` 是**必填布尔**，漏传会被反序列化成
     `false` → 被当成"非最后分片" → **接口返回 IsSuccess=true 且给了 FileId，但内容根本
     没进文件信息表**（回环下载报「数据库文件信息表中不存在编码为 X 的文件信息」）。
     必须显式传 `IsLast: true`。判断有没有真落库：`AttachmentDownLoad` 拉回来比字节。

  B. `NeedUpDateFields` 与「新增分录行」不能同时用：指定了 NeedUpDateFields 后，
     Model 里新加的其它实体行（如 FReimbAndRecInvInfo）会被**静默丢弃**。
     改已有明细用一次 Save（带 NeedUpDateFields），新增联动行必须**另起一次 Save**（不带）。

  C. Save 把 `ValidateFlag` 设为 `true` 时，`FCONTACTUNIT`（往来单位）变成**必填**，
     不填直接 MsgCode=11 报「字段"往来单位"是必填项」。helper 的 save() 默认
     ValidateFlag=false 所以不会报 —— 但真实单据应该补上（=申请人本人）。

字段名对照（很容易搞错）：
  View 实体名                Save 实体 Key           说明
  ER_ExpenseReimbEntry  →   FEntity                 报销明细
  RecInvInfo            →   FRecInvInfo             收票信息
  FReimbAndRecInvInfo   →   FReimbAndRecInvInfo     明细与发票联动关联
  BillHead              →   （表头字段直接写在 Model 根，没有包裹键）
  往来单位：View 显示 CONTACTUNIT，Save 键是 FCONTACTUNIT

用法：
  python expense_edit.py attach  100002 "D:/x/行程单.pdf"
  python expense_edit.py set-detail 100002 --entry 105001 --amount 8.00
  python expense_edit.py linkage 100002 --recv SPD00000002 --entry 105001
  python expense_edit.py check   100002
"""
import os
import sys
import json
import base64
import hashlib
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recvin_link import Kingdee, load_kingdee_config, FORM_EXPENSE, FORM_TRAVEL, FORM_RECV_INV  # noqa: E402

ENTRY_NAME = {"ER_ExpReimbursement": "ER_ExpenseReimbEntry",
              "ER_ExpReimbursement_Travel": "TravelReimbEntry"}   # 差旅单实体名到期实测再修


def pick_form(travel):
    return FORM_TRAVEL if travel else FORM_EXPENSE


# ────────────────────────── 附件 ──────────────────────────
def attach_file(kd, formid, fid, bill_no, file_path, alias=None, entry_key=None,
                entry_inter_id=None):
    """上传文件并挂到单据附件。返回 FileId。

    ⚠️ IsLast 必须 true，否则内容不落库（见模块头 A）。
    """
    with open(file_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode()
    fname = os.path.basename(file_path)

    payload = {"FileName": fname, "FormId": formid, "IsLast": True,
               "InterId": str(fid), "BillNO": bill_no, "SendByte": b64}
    if alias:
        payload["AliasFileName"] = alias
    # 单据体附件：Entrykey / EntryinterId 要么都给要么都不给
    if entry_key and entry_inter_id:
        payload["Entrykey"] = entry_key
        payload["EntryInterId"] = str(entry_inter_id)

    r = kd._svc("AttachmentUpLoad", formid, payload, timeout=180)
    res = r.get("Result", {}) or {}
    st = res.get("ResponseStatus", {}) or {}
    file_id = res.get("FileId")
    print(f"  AttachmentUpLoad IsSuccess={st.get('IsSuccess')} FileId={file_id}")
    for e in (st.get("Errors") or [])[:3]:
        print("    ERR:", str(e.get("Message"))[:200])
    if not file_id:
        raise SystemExit("附件上传未返回 FileId")

    # 回环校验：真下载一次，比 md5
    got, size, note = download_file(kd, formid, file_id)
    if got is None:
        print(f"  ⚠️ 回环下载失败：{note}")
    else:
        same = hashlib.md5(got).hexdigest() == hashlib.md5(raw).hexdigest()
        print(f"  回环校验：{size} 字节 与原件一致={same}"
              f"{'' if same else '  ← 内容不一致，检查 SendByte 编码/IsLast'}")
    return file_id


def download_file(kd, formid, file_id):
    """回环下载附件。返回 (bytes|None, size, note)。"""
    r = kd._svc("AttachmentDownLoad", formid, {"FileID": file_id}, timeout=180)
    res = r.get("Result", {}) or {}
    fp = res.get("FilePart")
    if fp:
        return base64.b64decode(fp), res.get("FileSize"), res.get("FileName")
    return None, 0, str(res.get("Message"))[:220]


# ───────────────────── 明细 ↔ 发票对齐 ─────────────────────
def set_detail(kd, formid, fid, entry_id, amount=None, exp_num=None, exp_id=None,
               tax_rate=None, tax_amt=None, inv_no=None, inv_date=None, remark=None):
    """改明细行。amount 一般 = min(发票价税合计, 申请金额)，且必须 ≤ 发票价税合计。

    ⚠️ 用 NeedUpDateFields 只更新这几个字段（否则会把必录的费用项目/部门清掉）。
    """
    need, model_entry = [], {"FEntryID": entry_id}

    def put(field, value):
        model_entry[field] = value
        need.append(field)

    if amount is not None:
        for f in ("FExpenseAmount", "FExpSubmitAmount", "FLocExpSubmitAmount", "FLOCNOTAXAMOUNT"):
            put(f, float(amount))
        put("FTaxSubmitAmt", round(float(amount) - float(tax_amt or 0), 2))
    if exp_id is not None:
        put("FExpID", {"Id": int(exp_id)})
    elif exp_num:
        put("FExpID", {"FNumber": str(exp_num)})
    if tax_rate is not None:
        put("FTaxRate", float(tax_rate))
    if tax_amt is not None:
        put("FTaxAmt", float(tax_amt))
    if inv_no:
        put("FInvNumber", inv_no)
    if inv_date:
        put("FInvOpenDate", inv_date)
    if remark is not None:
        put("FRemark", remark)

    ok, st = kd.save(formid, {"FID": fid, "FEntity": [model_entry]}, need_update=need)
    print(f"  改明细 entry={entry_id} -> IsSuccess={ok}")
    for e in (st.get("Errors") or [])[:3]:
        print("    ERR:", str(e.get("FieldName")), str(e.get("Message"))[:200])
    if not ok:
        print("   提示：若报「往来单位是必填项」，先跑 recvin_link.py set-contact 补上")
    return ok


def write_linkage(kd, formid, fid, recv_bill_no, entry_id, link_code=None,
                  inv_entry_ids=None):
    """写「明细 ↔ 发票分录」联动关系（= UI「合并生成费用明细」的底层数据）。

    ⚠️ 必须**单独一次 Save**，不能和 NeedUpDateFields 混（见模块头 B）。
    同时回填明细侧的 FReimbLinkInvCode / FRecInvBillNo / FInvNumber / FInvOpenDate
    —— 这几个在元数据里标了 IsNewLock/IsEditLock，但实测**可以写进去**。
    """
    # 1) 取收票单（发票）信息 + 各分录
    rows = kd.query(FORM_RECV_INV, "FID,FBillNo", f"FBillNo='{recv_bill_no}'", top=5)
    if not rows:
        raise SystemExit(f"找不到收票单 {recv_bill_no}")
    recv_fid = rows[0][0]
    rv = kd.view(FORM_RECV_INV, recv_fid)
    inv_no = rv.get("IVNUMBER")
    inv_date = str(rv.get("OPENDATE") or "")[:10]
    lines = [e for e in (rv.get("Entity") or []) if isinstance(e, dict)]
    if not lines:
        raise SystemExit(f"收票单 {recv_bill_no} 没有明细分录，无法建立联动")

    if link_code is None:
        link_code = hashlib.md5(f"{fid}-{recv_fid}".encode()).hexdigest()[:16]

    picked = [e for e in lines if not inv_entry_ids or e.get("Id") in inv_entry_ids]
    link_rows = [{
        "FReimbLinkRecInvCode": link_code,
        "FRecInvFid": recv_fid,
        "FRecInvEntryId": e.get("Id"),
        "FInvAllAmt": e.get("TOTALAMOUNT") or 0,
        "FInvAmt": e.get("AMOUNT") or 0,
        "FInvTaxAmt": e.get("TAXAMOUNT") or 0,
        "FRecInvoiceBillNo": recv_bill_no,
        "FInsurancePremium": 0.0,
    } for e in picked]

    # 2) 明细侧打标
    kd.save(formid, {"FID": fid, "FEntity": [{
        "FEntryID": entry_id, "FReimbLinkInvCode": link_code,
        "FRecInvBillNo": recv_bill_no, "FInvNumber": inv_no, "FInvOpenDate": inv_date}]},
        need_update=["FReimbLinkInvCode", "FRecInvBillNo", "FInvNumber", "FInvOpenDate"])
    print(f"  明细侧打标 link_code={link_code} 收票单={recv_bill_no} 发票号={inv_no}")

    # 3) 联动行（另起一次 Save，不带 NeedUpDateFields）
    ok, st = kd.save(formid, {"FID": fid, "FReimbAndRecInvInfo": link_rows})
    print(f"  联动行写入 {len(link_rows)} 行 -> IsSuccess={ok}")
    for e in (st.get("Errors") or [])[:3]:
        print("    ERR:", str(e.get("Message"))[:200])

    back = kd.view(formid, fid).get("FReimbAndRecInvInfo") or []
    print(f"  回读联动表 {len(back)} 行，价税合计 = {sum(x.get('FInvAllAmt') or 0 for x in back)}")
    return link_code, len(back)


# ───────────────────────── 综合体检 ─────────────────────────
def check(kd, formid, fid):
    o = kd.view(formid, fid)
    reimb = sum((r.get("ExpenseAmount") or 0) for r in (o.get("ER_ExpenseReimbEntry") or []))
    inv_rows = o.get("RecInvInfo") or []
    inv_amt = sum((x.get("RecInv") or {}).get("FSUMALLAMOUNT") or 0 for x in inv_rows)
    link = o.get("FReimbAndRecInvInfo") or []
    link_amt = sum(x.get("FInvAllAmt") or 0 for x in link)
    cu = o.get("CONTACTUNIT") or {}

    print("=" * 72)
    print(f"{o.get('BillNo')} (FID {fid}) 状态={o.get('DocumentStatus')}")
    print("=" * 72)
    print(f"  报销金额      : {reimb}")
    print(f"  发票价税合计  : {inv_amt}  （收票信息 {len(inv_rows)} 行）")
    print(f"  联动表        : {len(link)} 行，合计 {link_amt}")
    print(f"  往来单位      : {cu.get('Number')} {cu.get('Name')}")
    for r in (o.get("ER_ExpenseReimbEntry") or []):
        print(f"  明细 Seq{r.get('Seq')}: 金额={r.get('ExpenseAmount')} 税率={r.get('TaxRate')} "
              f"税额={r.get('TaxAmt')} 联动标识={r.get('FReimbLinkInvCode')!r}")

    print("\n  校验：")
    for name, ok, detail in [
        ("报销金额 ≤ 发票价税合计", bool(inv_rows) and reimb <= inv_amt + 1e-6, f"{reimb} ≤ {inv_amt}"),
        ("发票金额 ≥ 报销金额（金蝶硬要求）", bool(inv_rows) and inv_amt + 1e-6 >= reimb, f"{inv_amt} ≥ {reimb}"),
        ("往来单位已补", bool(cu), cu.get("Number")),
        ("联动表合计 = 发票合计", (not link) or abs(link_amt - inv_amt) < 0.005, link_amt),
    ]:
        print(f"    {'✅' if ok else '❌'} {name}  ({detail})")
    return {"reimb": reimb, "inv_amt": inv_amt, "link_rows": len(link), "contact": bool(cu)}


# ───────────────────────── CLI ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description="报销单明细↔发票对齐 / 联动 / 附件（纯金蝶 WebAPI）")
    ap.add_argument("--travel", action="store_true", help="操作差旅费报销单")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("attach", help="上传附件并挂到单据")
    s.add_argument("fid")
    s.add_argument("file", help="本地文件路径")
    s.add_argument("--bill-no", help="单据编号（默认自动查）")
    s.add_argument("--alias", help="附件别名")
    s.add_argument("--entry-key", help="单据体 Key（分录级附件时用）")
    s.add_argument("--entry-inter-id", type=int, help="分录内码（分录级附件时用）")

    s = sub.add_parser("set-detail", help="改明细金额/费用项目")
    s.add_argument("fid")
    s.add_argument("--entry", type=int, required=True, help="明细分录内码 FEntryID")
    s.add_argument("--amount", type=float, help="报销金额（应 ≤ 发票价税合计）")
    s.add_argument("--exp-num", help="费用项目编码")
    s.add_argument("--exp-id", type=int, help="费用项目内码")
    s.add_argument("--tax-rate", type=float, help="税率 %")
    s.add_argument("--tax-amt", type=float, help="税额")
    s.add_argument("--inv-no", help="发票号码（逗号分隔可多个）")
    s.add_argument("--inv-date", help="开票日期 YYYY-MM-DD")

    s = sub.add_parser("linkage", help="写明细↔发票联动关系")
    s.add_argument("fid")
    s.add_argument("--recv", required=True, help="收票单号，如 SPD00000002")
    s.add_argument("--entry", type=int, required=True, help="明细分录内码 FEntryID")
    s.add_argument("--link-code", help="联动标识（默认由 fid+收票单内码 派生 16 位）")

    s = sub.add_parser("download", help="回环下载附件（校验用）")
    s.add_argument("file_id")
    s.add_argument("--out", help="保存到本地路径")

    s = sub.add_parser("check", help="综合体检")
    s.add_argument("fid")

    # 让 --travel 写在子命令「前」或「后」都能生效
    # （default=SUPPRESS：子命令未显式给出时不覆盖顶层已解析的值）
    for _sp in sub.choices.values():
        _sp.add_argument("--travel", action="store_true", default=argparse.SUPPRESS,
                         help="操作差旅费报销单（写在子命令前后均可）")

    a = ap.parse_args()
    kd = Kingdee(load_kingdee_config())
    formid = pick_form(a.travel)

    if a.cmd == "attach":
        bill_no = a.bill_no
        if not bill_no:
            o = kd.view(formid, a.fid)
            bill_no = o.get("BillNo")
        print(f"→ 上传附件到 {formid} {a.fid} ({bill_no})：{os.path.basename(a.file)}")
        attach_file(kd, formid, a.fid, bill_no, a.file, a.alias, a.entry_key, a.entry_inter_id)
    elif a.cmd == "set-detail":
        print(f"→ 调整明细 {a.fid} entry={a.entry}")
        set_detail(kd, formid, a.fid, a.entry, a.amount, a.exp_num, a.exp_id,
                   a.tax_rate, a.tax_amt, a.inv_no, a.inv_date)
    elif a.cmd == "linkage":
        print(f"→ 写联动 {a.fid} ← {a.recv}")
        write_linkage(kd, formid, a.fid, a.recv, a.entry, a.link_code)
    elif a.cmd == "download":
        got, size, note = download_file(kd, formid, a.file_id)
        if got is None:
            print(f"❌ {note}")
        else:
            print(f"✅ {size} 字节  {note}")
            if a.out:
                with open(a.out, "wb") as f:
                    f.write(got)
                print(f"   已存到 {a.out}")
    elif a.cmd == "check":
        check(kd, formid, a.fid)


if __name__ == "__main__":
    main()
