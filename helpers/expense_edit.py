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

# 明细实体名（2026-09-22 实测更正）：
#   差旅费报销单 ER_ExpReimbursement_Travel 的明细 View 实体名**也是** ER_ExpenseReimbEntry，
#   不是 ER_ExpTravelReimbEntry，也不是 TravelReimbEntry（旧值写错，已修）。
ENTRY_NAME = {"ER_ExpReimbursement": "ER_ExpenseReimbEntry",
              "ER_ExpReimbursement_Travel": "ER_ExpenseReimbEntry"}

# 差旅单明细的「差旅费金额」字段（Save 用）。
# ⚠️ 是 FTravelAmount，**不是** FExpTravelAmount（SKILL.md 旧版记错，已修）。
# View 返回的键名就是 FTravelAmount（带 F），与 FInvNumber / FTravelType 同类。
TRAVEL_AMOUNT_FIELD = "FTravelAmount"


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
    """改明细行。amount = **含税**费用金额（= 价税合计），且必须 ≤ 发票价税合计。

    勾稽公式（2026-09-22 实测 101731 六行全部吻合，覆盖税率 9% / 1% / 0%）：
        含税(ExpenseAmount) = 不含税(TaxSubmitAmt) + 税额(TaxAmt)
        `TaxSubmitAmt` 这个名字是**反的** —— 它是「不含税金额」，不是「可抵扣税额」。
    所以只需给 amount(含税) + tax_amt(税额)，不含税由本函数自己算，别在外面算。

    🔴 差旅单（ER_ExpReimbursement_Travel）还要同步写 `FTravelAmount`（差旅费金额
       = 含税金额），否则报「差旅费金额不等于税额加费用金额」。
       —— 旧版**整个漏了这个字段**，同时把 `FLOCNOTAXAMOUNT`(不含税) 误写成含税值，两处已修。

    🔴🔴 **副作用警告：成功的写入会让「暂存(Z)」单流转成「已提交(A)」**（2026-09-22 实测）：
       · `101401`（Z，无单号）写入成功后 → 状态变 `A` 并分配单号 `CLFBX00000001`；
       · 对照组 `101464` / `101404`：写入**失败**（反写超限，见下）→ **仍是 Z**。
       ⇒ 差别就在"写入成功没成功"。虽然只写金额，但单据会**推进状态**，而 `A` 之后
         WebAPI **撤不回**（`UnSubmit` 不存在 / `UnAudit` 报"已关联工作流实例"）。
       ⇒ **绝不要拿别人的草稿单做写入测试**。要测写入：**自建一张测试单**（`Save` 新建 → Z），
         测完 `Delete`（Delete 只放 `Z`/`A`/`D`）。
       ⇒ 生产里对真实单跑 `fit --apply` 前，先确认该单**本来就该进入提交态**，或由用户点头。

    ⚠️ 用 NeedUpDateFields 只更新这几个字段（否则会把必录的费用项目/部门清掉）。
    """
    need, model_entry = [], {"FEntryID": entry_id}

    def put(field, value):
        model_entry[field] = value
        need.append(field)

    if amount is not None:
        gross = float(amount)                     # 含税 = 价税合计
        tax = float(tax_amt or 0)                 # 税额
        net = round(gross - tax, 2)               # 不含税
        for f in ("FExpenseAmount", "FExpSubmitAmount", "FLocExpSubmitAmount"):
            put(f, gross)
        put("FTaxSubmitAmt", net)                 # 不含税（名字反直觉，实测如此）
        put("FLOCNOTAXAMOUNT", net)               # ← 修：旧版误写成 gross
        if formid == FORM_TRAVEL:
            put(TRAVEL_AMOUNT_FIELD, gross)       # ← 补：旧版整个漏了
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


def fit_to_invoice(kd, formid, fid, target=None, mode="sequential",
                   entry_ids=None, apply=False):
    """按发票金额**调低**报销金额 —— 合规替代「越狱」的正路。

    场景（2026-09-22 用户诉求 A）：报销金额 1620 > 发票价税合计 1532.06。
    金蝶 UI 拦「发票金额不允许小于报销金额！」。**正确做法是把报销金额改成发票金额**，
    而不是关掉校验绕过去（那会造出审核必退的单）。

    target    : 目标报销金额。默认 = 该单收票信息的发票价税合计。
    mode      : sequential = 从**最后一行往前**依次冲减（贴近 UI「按发票金额调整」）
                ratio      = 各行按原比例缩放
    entry_ids : 只调这几行（默认全部明细行）
    apply     : **默认 False 只算不写**（dry-run）；确认无误再加 --apply 真写。

    🔴🔴 **`--apply` 的副作用：成功写入会把「暂存(Z)」单推进成「已提交(A)」**（2026-09-22 实测）：
       `101401`(Z) 写入成功 → 变 `A` + 分配单号；而写入**失败**的 `101464`/`101404` 仍是 `Z`。
       `A` 之后 WebAPI **撤不回**。⇒ 对真实单 `--apply` 前必须确认它本来就该进提交态，
       或先让用户点头；**要测写入请自建测试单，别拿别人的草稿单**。
       **dry-run（不带 `--apply`）是纯读，不触发任何状态变化** —— 拿不准就先 dry-run。

    ⚠️ 调低含税金额时，税额**按同比例缩放**（保持税率不变），否则不含税会算错。
       （实测勾稽：含税 = 不含税 + 税额，见 set_detail docstring）

    🔴 方向不对称（2026-09-22 实测，重要）：
       · **调低 = 安全**。这正是本函数的主用途，不会踩任何校验。
       · **调高 = 受限**。若明细来自出差申请单下推，调高会触发反写限额
         「关联费用申请金额超出源单已下推报销金额！」而被金蝶拒绝 ——
         实测 101464 / 101404 两张 Z 单就是这么失败的。
         所以想"多报"只能回申请单加额度，不能在报销单上加。

    返回 {ok, before, after, target, changes:[(eid,seq,old,new,tax_old,tax_new)], applied}
    """
    o = kd.view(formid, fid)
    ents = [e for e in (o.get(ENTRY_NAME.get(formid, "ER_ExpenseReimbEntry")) or [])
            if isinstance(e, dict)]
    if not ents:
        raise SystemExit("该单没有明细分录，无法调整")

    inv_rows = [r for r in (o.get("RecInvInfo") or []) if isinstance(r, dict)]
    inv_amt = round(sum(float((r.get("RecInv") or {}).get("FSUMALLAMOUNT") or 0)
                        for r in inv_rows), 2)
    before = round(sum(float(e.get("ExpenseAmount") or 0) for e in ents), 2)

    if target is None:
        target = inv_amt
        src = "收票信息发票价税合计 %s" % inv_amt
    else:
        target = round(float(target), 2)
        src = "显式指定"

    print("=" * 92)
    print("%s (FID %s)  状态=%s" % (o.get("BillNo"), fid, o.get("DocumentStatus")))
    print("=" * 92)
    print("  发票价税合计 : %s  （收票信息 %d 行）" % (inv_amt, len(inv_rows)))
    print("  当前报销金额 : %s" % before)
    print("  目标报销金额 : %s   ← %s" % (target, src))

    if inv_amt > 0 and target > inv_amt + 1e-6:
        print("  ⛔ 目标 %s > 发票合计 %s —— 这会触发「发票金额不允许小于报销金额」，拒绝执行"
              % (target, inv_amt))
        return {"ok": False, "before": before, "after": before, "target": target,
                "changes": [], "applied": False,
                "msg": "目标金额超过发票价税合计"}
    if inv_amt <= 0:
        if target > before + 1e-6:
            print("  ⛔ 该单还没有收票信息（发票合计 0），**不能调高**报款金额 ——"
                  " 上调会触发源单反写限额「关联费用申请金额超出源单已下推报销金额」"
                  "（2026-09-22 实测），且没有发票可依据。请先把发票挂到收票信息。")
            return {"ok": False, "before": before, "after": before, "target": target,
                    "changes": [], "applied": False, "msg": "无收票信息时不允许调高"}
        print("  ⚠️ 该单还没有收票信息（发票合计 0）—— 本次只按你显式给的目标调低，"
              "无法核对发票金额。挂上发票后请复查 `check`。")
    if abs(target - before) < 0.005:
        print("  ✅ 已经一致，无需调整")
        return {"ok": True, "before": before, "after": before, "target": target,
                "changes": [], "applied": False, "msg": "无需调整"}

    want = [e for e in ents
            if not entry_ids or e.get("Id") in entry_ids or e.get("FEntryID") in entry_ids]
    old_sum = round(sum(float(e.get("ExpenseAmount") or 0) for e in want), 2)
    delta = round(target - before, 2)          # 负数 = 要调低

    plan = []
    if mode == "ratio":
        if old_sum <= 0:
            raise SystemExit("选中行金额合计为 0，无法按比例缩放")
        acc = 0.0
        for i, e in enumerate(want):
            old = float(e.get("ExpenseAmount") or 0)
            if i == len(want) - 1:
                new = round(target - acc, 2)   # 末行兜住分位差
            else:
                new = round(old * target / old_sum, 2)
                acc += new
            plan.append((e, old, max(0.0, new)))
    else:  # sequential：从最后一行往前冲减
        remain = abs(delta)
        alloc = {}
        for e in reversed(want):
            old = float(e.get("ExpenseAmount") or 0)
            cut = min(old, remain) if delta < 0 else 0.0
            alloc[id(e)] = round(cut, 2)
            remain = round(remain - cut, 2)
            if remain <= 0:
                break
        if remain > 0.005:
            print("  ⛔ 选中行的可冲减额度不够，还差 %s —— 请把更多行纳入或改 mode" % remain)
            return {"ok": False, "before": before, "after": before, "target": target,
                    "changes": [], "applied": False, "msg": "可冲减额度不足"}
        for e in want:
            old = float(e.get("ExpenseAmount") or 0)
            plan.append((e, old, round(old - alloc.get(id(e), 0.0), 2)))

    print()
    print("  %-4s %-10s %-14s %-14s %-12s %-12s" %
          ("Seq", "FEntryID", "含税(旧→新)", "税额(旧→新)", "税率", "差旅费金额"))
    changes = []
    for e, old, new in plan:
        old_tax = float(e.get("TaxAmt") or 0)
        rate = float(e.get("TaxRate") or 0)
        new_tax = round(old_tax * new / old, 2) if old > 0 else 0.0
        old_tr = e.get(TRAVEL_AMOUNT_FIELD)
        print("  %-4s %-10s %-14s %-14s %-12s %-12s" % (
            e.get("Seq"), e.get("Id"),
            "%s→%s" % (old, new), "%s→%s" % (old_tax, new_tax), rate,
            "%s→%s" % (old_tr, new)))
        changes.append({"entry_id": e.get("Id"), "seq": e.get("Seq"),
                        "old": old, "new": new, "tax_old": old_tax, "tax_new": new_tax})
    after = round(before + delta, 2)
    print()
    if inv_amt > 0:
        print("  合计: %s → %s   （发票 %s，余量 %s）" % (before, after, inv_amt,
                                                      round(inv_amt - after, 2)))
    else:
        print("  合计: %s → %s   （该单暂无收票信息，无法核对发票）" % (before, after))

    if not apply:
        print()
        print("  ℹ️ dry-run：以上**未写入**。确认无误后加 --apply 真写。")
        return {"ok": True, "before": before, "after": after, "target": target,
                "changes": changes, "applied": False, "msg": "dry-run"}

    print()
    print("  写 入：")
    all_ok = True
    for c in changes:
        if abs(c["new"] - c["old"]) < 0.005:
            continue
        ok = set_detail(kd, formid, fid, c["entry_id"],
                        amount=c["new"], tax_amt=c["tax_new"])
        all_ok = all_ok and ok
    o2 = kd.view(formid, fid)
    e2 = o2.get(ENTRY_NAME.get(formid, "ER_ExpenseReimbEntry")) or []
    real = round(sum(float(x.get("ExpenseAmount") or 0) for x in e2), 2)
    print()
    print("  回读报销金额 = %s（目标 %s，表头 ExpAmountSum=%s）"
          % (real, target, o2.get("ExpAmountSum")))
    return {"ok": all_ok and abs(real - target) < 0.02,
            "before": before, "after": real, "target": target,
            "changes": changes, "applied": True}


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
        ("报销金额 ≤ 发票价税合计（金蝶硬要求）", bool(inv_rows) and reimb <= inv_amt + 1e-6,
         f"{reimb} ≤ {inv_amt}"),
        ("往来单位已补", bool(cu), cu.get("Number")),
        ("联动表合计 = 发票合计", (not link) or abs(link_amt - inv_amt) < 0.005, link_amt),
    ]:
        print(f"    {'✅' if ok else '❌'} {name}  ({detail})")

    # ── 明细金额勾稽自检（2026-09-22 新增）──
    # 实测公式：含税 ExpenseAmount = 不含税 TaxSubmitAmt(==LOCNOTAXAMOUNT) + 税额 TaxAmt
    # 差旅单另需 FTravelAmount == ExpenseAmount。
    # 这条自检同时是 FTravelAmount / FLOCNOTAXAMOUNT 两个 Save 键名的**安全网**：
    # 键名若写错，金蝶会静默忽略（不报错），但回读值会对不上 → 这里就会亮红。
    print("\n  明细勾稽自检（含税 = 不含税 + 税额；差旅单另需 差旅费金额 = 含税）：")
    for r in (o.get(ENTRY_NAME.get(formid, "ER_ExpenseReimbEntry")) or []):
        gross = float(r.get("ExpenseAmount") or 0)
        tax = float(r.get("TaxAmt") or 0)
        net = float(r.get("TaxSubmitAmt") or 0)
        loc_net = r.get("LOCNOTAXAMOUNT")
        tr = r.get(TRAVEL_AMOUNT_FIELD)
        bad = []
        if abs((net + tax) - gross) > 0.02:
            bad.append("不含税+税额(%s+%s=%s) ≠ 含税 %s" % (net, tax, round(net + tax, 2), gross))
        if loc_net is not None and abs(float(loc_net) - net) > 0.02:
            bad.append("LOCNOTAXAMOUNT %s ≠ 不含税 %s" % (loc_net, net))
        if formid == FORM_TRAVEL and tr is not None and abs(float(tr) - gross) > 0.02:
            bad.append("FTravelAmount %s ≠ 含税 %s" % (tr, gross))
        flag = "✅" if not bad else "❌"
        print("    %s Seq%s: 含税=%s 税额=%s 不含税=%s 差旅费金额=%s"
              % (flag, r.get("Seq"), gross, tax, net, tr))
        for b in bad:
            print("        ⚠️ %s" % b)
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
    s.add_argument("--tax-rate", type=float, help="税率（百分数，如 6 表示 6%%）")
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

    s = sub.add_parser("fit", help="按发票金额调低报销金额（合规替代越狱）")
    s.add_argument("fid")
    s.add_argument("--target", type=float,
                   help="目标报销金额（默认取收票信息发票价税合计）")
    s.add_argument("--mode", choices=["sequential", "ratio"], default="sequential",
                   help="sequential=从最后一行往前冲减（默认）；ratio=按比例缩放")
    s.add_argument("--entry", type=int, action="append",
                   help="只调这个明细行（可重复给多次）")
    s.add_argument("--apply", action="store_true", help="真写（不加则只算不写）")

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
    elif a.cmd == "fit":
        r = fit_to_invoice(kd, formid, a.fid, target=a.target, mode=a.mode,
                           entry_ids=a.entry, apply=a.apply)
        if not r["ok"]:
            print("❌ 未完成：%s" % r.get("msg", ""))
            raise SystemExit(2)
    elif a.cmd == "check":
        check(kd, formid, a.fid)


if __name__ == "__main__":
    main()
