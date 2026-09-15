# -*- coding: utf-8 -*-
"""recvin_link.py — 报销单「收票信息」写入 · 纯金蝶 WebAPI 版（已实测打通）

════════════════════════════════════════════════════════════════════════
为什么这个脚本重要
════════════════════════════════════════════════════════════════════════
长期结论是「报销单的收票信息 FRecInv 被字段级锁定，标准 WebAPI 写不进」，
因此整条「员工不登录金蝶就报销」的链路被认为必须依赖发票云（piazzone）API，
并因发票云的测试授权门槛 + 生产域名被网关按 SNI 拦截而搁置。

2026-09-14 实测推翻该结论：**收票信息可以用纯金蝶 WebAPI 写入**，
且写入结果与金税互联/发票助手插件写入的结果逐字段一致
（收票单侧 LINKBILLTYPE/LINKBILLID/LINKIVNUMBER/LINKBILLDATE 自动回填）。
**不需要发票云 API，不需要 websocket，不需要任何外部服务。**

之前三次失败的真实原因（都不是"字段被锁"）：
  1. **字段层级放错**：FRecInv / FIVSerialNo 必须放在 `Model.FRecInvInfo[]` 数组行内。
     放到单据头层级会报：
       ResolveFiled_InnerEx解析字段(Key:FIVSerialNo…)…实体不存在此属性！[EntityType：BillHead…]
  2. **死磕 FIVSerialNo**：官方流程产物的 FIVSERIALNO 是空的（实测 101714/101716 均为 " "）。
     真正承载关联的是 `FRecInv`（收票单）。FIVSerialNo 是可选装饰，不是入口。
     ⚠️ **但这条只在「同组织」成立**（101714/101716 都是本组织的票）：
     跨组织时，界面要拿**报销单组织的税号**去发票云解析这张票，
     而票是别人的 → 取不到发票云流水号 → 报「驳回：无法获取当前关联收票单的
     发票云发票流水号」→ 单据变 `D`。详见 `guard_recv_invoices()`。
  3. **误读元数据锁**：FRecInv 的 IsNewLock/IsEditLock 均为 True，据此判定"必然写不进"。
     实测**该锁定不阻止 WebAPI Save 写入分录字段**。不要只凭 IsNewLock 下结论，要实测。

════════════════════════════════════════════════════════════════════════
🔴 边界：能写进去 ≠ 单据可用（2026-09-15 实测，务必先读）
════════════════════════════════════════════════════════════════════════
本脚本走的是「**直接 Save 报销单的 FRecInvInfo 子表**」这一条路。
金蝶 WebAPI 的 18 个 Operation 里**没有**「选择发票 / 补发票」——
发票云取票 + 回写流水号是**纯 UI 交互**，WebAPI 没开放。
所以直接写子表 = **跳过了发票云的取票与组织归属校验**，校验被推迟到界面环节
（用户实测触发点是**"单据被驳回后在收票信息页签重新上传/补发票"**，报
「无法获取当前关联收票单的发票云发票流水号」；打开/审核/结算同样取不到）：

  Save ✅ → Submit ✅（当场读到 B）→ **界面 ❌ → 单据变 D（重新审核＝操作被拒）**

⇒ **别指望金蝶接口会返回失败**，"提交不过去"这件事必须由本脚本自己拦（`submit` 会先跑 `precheck`）。

硬约束（`guard_recv_invoices()` 会在写入前拦住）：
  · 收票单 `SOURCEORGID`（购方/来源组织）**必须 = 报销单组织**。挂票只改写 `SETTLEORGID`，
    **改不掉"这张票是别人的"**（`PURNAME` 仍是原购方）→ 跨组织必然被驳回。
  · 收票单要是**发票云归集**来的：`FPDFURL` / `FPIAOZONESERIALNUMBER` 非空。
  · 目标组织**收票服务许可**须在有效期内（实测示例二科技 105 已过期，
    发票云报「…【收票服务】许可已过期失效…[0300]」）。
  需绕过时用 `--allow-cross-org` / `--allow-no-piaozone` 显式声明（默认全拦）。

════════════════════════════════════════════════════════════════════════
已验证事实（示例科技生产环境，2026-09-14）
════════════════════════════════════════════════════════════════════════
· 报销单 101717 (FYBX20260101000002) 写入收票单 SPD00008518 → RecInvInfo 1 行 ✅
· 联动自动回填：118519.LINKBILLTYPE=ER_ExpReimbursement, LINKBILLID=101717,
              LINKIVNUMBER=FYBX20260101000002, LINKBILLDATE=2026-09-14 ✅
· 与官方流程产物对比（101716 ← 118632/118633）逐字段一致 ✅
· 费用明细 FEntity(=ER_ExpenseReimbEntry) 未被 IsDeleteEntry=true 误删 ✅
· 只需收票单号即可，FIVSerialNo 可不写（官方也不写）✅ —— **仅限同组织**（见上「边界」）
· ⚠️ 反面实测（2026-09-15）：跨组织挂票 Save/Submit 均成功，但单据全部被驳回为 `D`。

════════════════════════════════════════════════════════════════════════
实体名映射（View 用 EntryName，Save 用 Key —— 一直是踩坑点）
════════════════════════════════════════════════════════════════════════
  表单                         Save Key          View EntryName
  费用报销单 ER_ExpReimbursement
                               FBillHead         BillHead
                               FEntity           ER_ExpenseReimbEntry
                               FRecInvInfo       RecInvInfo      ← 收票信息
                               FEInvoiceEntity   FEInvoiceEntity
                               FReimbAndRecInvInfo  FReimbAndRecInvInfo
  差旅费报销单 ER_ExpReimbursement_Travel：结构完全相同，同一写法通用
  费用申请单 ER_ExpenseRequest：**没有**收票信息实体，别往它上面写

════════════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════════════
# 1) 按发票号码查收票单（拿到收票单号）
python recvin_link.py find 24000000000000000002

# 2) 查看报销单当前收票信息
python recvin_link.py list 101717

# 3) 把收票单挂到报销单「收票信息」（追加，不删已有行）
python recvin_link.py link 101717 SPD00008518

#    覆盖模式（清掉未列出的行，用于纠错）
python recvin_link.py link 101717 SPD00008518,SPD00008631 --replace

#    差旅费报销单（--travel 放子命令前或后都可以）
python recvin_link.py --travel link 101717 SPD00008518

#    连发票云流水号一起写（可选；官方流程留空，一般不需要）
python recvin_link.py link 101717 SPD00008518 --with-serial

# 4) 一键自检：写→读→校验收票单侧联动
python recvin_link.py verify 101717 SPD00008518

配置来源（优先级）：
  1) 环境变量 KINGDEE_BASE_URL / KINGDEE_ACCTID / KINGDEE_USERNAME / KINGDEE_PASSWORD
  2) kingdee-data-exporter 的 config.py 里的 KINGDEE_CONFIG（本机默认）
分发给他司时，用环境变量即可，无需改代码。
"""
import os
import re
import sys
import json
import time
import argparse
import http.cookiejar
import urllib.request
import urllib.error

# ────────────────────────── 配置 ──────────────────────────
KSVC_SUFFIX = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService."
AUTH_SUFFIX = "Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc"

FORM_EXPENSE = "ER_ExpReimbursement"           # 费用报销单
FORM_TRAVEL = "ER_ExpReimbursement_Travel"     # 差旅费报销单
FORM_RECV_INV = "IV_ReceivedInvoice"           # 收票单

# 两类报销单的收票信息实体（Save Key / View EntryName）——结构相同
RECV_ENTITY = {"save_key": "FRecInvInfo", "view_name": "RecInvInfo"}


def load_kingdee_config():
    """环境变量优先；否则回退到同机 kingdee-data-exporter 的 config.py。"""
    cfg = {
        "base_url": os.environ.get("KINGDEE_BASE_URL", ""),
        "acctid": os.environ.get("KINGDEE_ACCTID", ""),
        "username": os.environ.get("KINGDEE_USERNAME", ""),
        "password": os.environ.get("KINGDEE_PASSWORD", ""),
        "lcid": int(os.environ.get("KINGDEE_LCID", 2052)),
    }
    if cfg["base_url"] and cfg["acctid"] and cfg["username"]:
        return cfg
    for cand in (r"C:\Users\Administrator\.workbuddy\skills\kingdee-data-exporter",
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if os.path.exists(os.path.join(cand, "config.py")):
            sys.path.insert(0, cand)
            try:
                from config import KINGDEE_CONFIG as KC
                return {**cfg, **{k: v for k, v in KC.items() if v}}
            except Exception:
                pass
    raise SystemExit("未找到金蝶配置：请设 KINGDEE_BASE_URL/KINGDEE_ACCTID/KINGDEE_USERNAME/KINGDEE_PASSWORD")


class Kingdee:
    """极简金蝶 WebAPI 客户端（登录 / 查询 / 查看 / 保存），仅用标准库。

    ⚠️ 必须带 CookieJar：金蝶登录后靠会话 Cookie 认身份，用裸 urlopen 会拿到
       「会话信息已丢失，请重新登录」（MsgCode 1），且 View 会返回空字典、不报错。
    """

    def __init__(self, cfg):
        self.base = cfg["base_url"].rstrip("/") + "/k3cloud/"
        self.cfg = cfg
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        self._login()

    # ── 底层 HTTP ──
    def _post(self, path, payload, timeout=90, retries=5, retry_wait=6):
        """POST 到 WebAPI。

        ⚠️ **出口代理会偶发瞬时故障**，实测形态：
          - `urllib.error.URLError: <urlopen error Tunnel connection failed: 502 Bad Gateway>`
          - `urllib.error.URLError: <urlopen error _ssl.c:1015: The handshake operation timed out>`
        这不是金蝶的问题，重试即可通（2026-09-15 实测连错 4 次后第 5 次成功）。
        所以这里默认 **重试 5 次、每次间隔 6s**，避免把网络抖动误报成业务失败。
        """
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last = None
        for i in range(max(1, retries)):
            req = urllib.request.Request(
                self.base + path, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if i < retries - 1:
                    time.sleep(retry_wait)
        raise SystemExit(f"网络异常（已重试 {retries} 次仍失败）：{last}")

    def _svc(self, method, formid, data_obj, timeout=90):
        return self._post(KSVC_SUFFIX + method + ".common.kdsvc", {
            "formid": formid,
            "data": json.dumps(data_obj, ensure_ascii=False)}, timeout=timeout)

    def _login(self):
        r = self._post(AUTH_SUFFIX, {
            "acctid": self.cfg["acctid"], "username": self.cfg["username"],
            "password": self.cfg["password"], "lcid": self.cfg.get("lcid", 2052)}, timeout=30)
        if r.get("LoginResultType") != 1:
            raise SystemExit(f"金蝶登录失败：{json.dumps(r, ensure_ascii=False)[:300]}")

    # ── 业务接口 ──
    @staticmethod
    def _unwrap_error(r):
        """金蝶出错时返回 [[{"Result":{"ResponseStatus":{...Errors...}}}]] 形状的包裹体。

        ⚠️ 2026-09-15 修：外层常是**双层 list**（`[[{...}]]`）——旧实现只判 `r[0]` 是不是
        dict，遇到嵌套 list 直接跳过，于是 `query()` 里
        `[row for row in r if isinstance(row, list)]` 会把**那个错误对象当成一行业务数据返回**，
        不报错、不抛异常，调用方以为查到了数据。典型触发：字段名写错
        （如给报销单查 `FCostOrgID`，实际该表单没有这个字段）。
        现在改为**先剥掉所有嵌套 list** 再判，杜绝这类静默错。
        """
        node = r
        while isinstance(node, (list, tuple)):
            if not node:
                return
            node = node[0]
        if not isinstance(node, dict):
            return
        st = (node.get("Result", {}) or {}).get("ResponseStatus", {}) or {}
        if not st.get("IsSuccess", True):
            msg = json.dumps(st.get("Errors", []), ensure_ascii=False)[:300]
            raise SystemExit(f"金蝶接口报错（MsgCode={st.get('MsgCode')}）：{msg}")

    def query(self, formid, fields, filter_string="", top=100, order=""):
        """ExecuteBillQuery。fields 用 View 风格名（如 FBillNo / FIVNUMBER）。返回二维列表。"""
        r = self._svc("ExecuteBillQuery", formid, {
            "FormId": formid, "FieldKeys": fields, "FilterString": filter_string,
            "OrderString": order, "TopRowCount": top, "StartRow": 0, "Limit": top})
        self._unwrap_error(r)
        if not isinstance(r, list):
            raise SystemExit(f"查询返回非预期结构：{json.dumps(r, ensure_ascii=False)[:300]}")
        return [row for row in r if isinstance(row, list)]

    def view(self, formid, fid, create_org=0):
        r = self._svc("View", formid, {"FormId": formid, "CreateOrgId": create_org,
                                       "Id": str(fid), "IsSortBySeq": "false"}, timeout=60)
        self._unwrap_error(r)
        out = (r.get("Result", {}) or {}).get("Result", {}) or {}
        if not out:
            raise SystemExit(f"View 返回空（单据 {fid} 不存在 / 无权限 / 会话失效）")
        return out

    def save(self, formid, model, is_delete_entry=False, need_update=None, timeout=90,
             retries=4, retry_wait=8):
        """标准 Save。返回 (是否成功, ResponseStatus)。

        ⚠️ **瞬时的「单据编辑冲突」会自动重试**（retries 次，每次间隔 retry_wait 秒）：
           报错形如 `"XXX"使用业务单据："费用报销单"业务操作-"[费用报销单-FYBX...-修改]"冲突，请稍候再使用。`
           这是编辑互斥锁的瞬时占用（并发/上一会话残留），**实测重试 1 次即成功**，
           **不需要**像旧笔记说的那样"关闭客户端或重下推新单"。tools/skill 里别再下那种结论。
        """
        last_st = {}
        for attempt in range(max(1, retries)):
            r = self._svc("Save", formid, {
                "NeedUpDateFields": need_update or [], "NeedReturnFields": [],
                "IsDeleteEntry": "true" if is_delete_entry else "false",
                "SubSystemId": "", "IsVerifyBaseDataField": "false", "IsEntryBatchFill": "true",
                "ValidateFlag": "false", "NumberSearch": "true", "IsAutoAdjustField": "true",
                "InterationFlags": "", "IgnoreInterationFlag": "true", "IsControlPrecision": "false",
                "ValidateRepeatJson": "true", "Model": model}, timeout=timeout)
            st = (r.get("Result", {}) or {}).get("ResponseStatus", {}) or {}
            if st.get("IsSuccess"):
                if attempt:
                    print(f"  （第 {attempt+1} 次尝试成功，前几次为瞬时编辑冲突）")
                return True, st
            last_st = st
            msgs = json.dumps(st.get("Errors", []), ensure_ascii=False)
            if ("冲突" in msgs or "请稍候再使用" in msgs) and attempt < retries - 1:
                print(f"  [!] 编辑锁冲突，{retry_wait}s 后重试（{attempt+1}/{retries}）…")
                time.sleep(retry_wait)
                continue
            break
        return False, last_st

    # ── 收票单 ──
    # ExecuteBillQuery 的字段名易踩坑（View 里叫 PDFURL/PURNAME，查询要写 FPDFURL/FPURNAME；
    # 名字写错会直接报「元数据中标识为 XXX 的字段不存在」。以下是逐字段实测通过的清单。
    # 另注：返回的列顺序 = FieldKeys 顺序（实测 A/B/C 三种排列均对应），可安全按位置解析。
    RECV_FIELDS = ("FID,FBillNo,FIVNUMBER,FSUMALLAMOUNT,FOPENDATE,FSALENAME,FPURNAME,"
                   "FLINKBILLTYPE,FLINKBILLID,FLINKIVNUMBER,FPDFURL,FPIAOZONESERIALNUMBER,"
                   "FSOURCEORGID.FNumber,FSOURCEORGID.FName,FSETTLEORGID.FNumber")

    def find_received_invoice(self, invoice_no=None, recv_bill_no=None, top=20):
        """按发票号码或收票单号查收票单。返回 [{fid, bill_no, invoice_no, amount, open_date,
        seller, buyer, link_bill_type, link_bill_id, link_iv, pdf_url, serial,
        src_org_no, src_org_name, settle_org_no}]"""
        conds = []
        if invoice_no:
            conds.append(f"FIVNUMBER='{invoice_no}'")
        if recv_bill_no:
            conds.append(f"FBillNo='{recv_bill_no}'")
        if not conds:
            raise ValueError("至少要给 发票号码 或 收票单号")
        rows = self.query(FORM_RECV_INV, self.RECV_FIELDS, " and ".join(conds), top)
        out = []
        for r in rows:
            if len(r) < 15:
                print(f"  [!] 查询返回列数异常（{len(r)}），跳过：{r}")
                continue
            out.append({"fid": r[0], "bill_no": r[1], "invoice_no": r[2], "amount": r[3],
                        "open_date": r[4], "seller": r[5], "buyer": r[6],
                        "link_bill_type": r[7], "link_bill_id": r[8], "link_iv": r[9],
                        "pdf_url": r[10], "serial": r[11],
                        "src_org_no": r[12], "src_org_name": r[13], "settle_org_no": r[14]})
        return out

    # ── 报销单收票信息 ──
    def list_linked(self, bill_fid, formid=FORM_EXPENSE):
        """读报销单当前收票信息行。"""
        o = self.view(formid, bill_fid)
        rows = o.get(RECV_ENTITY["view_name"]) or []
        return rows if isinstance(rows, list) else []

    def bill_org_no(self, bill_fid, formid=FORM_EXPENSE):
        """读报销单的组织编号（如 '104'）+ 组织名。返回 (编号, 名称, 单据View)。

        ⚠️ View 里 `OrgID` 是 dict，但 `OrgID.Name` 是**多语言列表**
        `[{'Key':2052,'Value':'…'}]`（`Number` 仍是字符串），直接打印会看到一坨 list。
        """
        o = self.view(formid, bill_fid)
        org = o.get("OrgID") or {}

        def _flat(v):
            if isinstance(v, list):
                if not v:
                    return ""
                for x in v:
                    if isinstance(x, dict) and x.get("Key") == 2052:
                        return str(x.get("Value") or "")
                first = v[0]
                return str(first.get("Value") if isinstance(first, dict) else first)
            if isinstance(v, dict):
                return str(v.get("Value") or v.get("Name") or "")
            return str(v or "")

        if isinstance(org, list):
            org = org[0] if org else {}
        return _flat(org.get("Number")), _flat(org.get("Name")), o

    def guard_recv_invoices(self, bill_fid, nos, formid=FORM_EXPENSE,
                            allow_cross_org=False, allow_no_piaozone=False):
        """挂票**前置体检**（2026-09-15 新增，血泪教训）。

        返回 (blocks, warns, rows)：blocks 非空就不该写。

        ── 为什么必须查这个 ──────────────────────────────────────────
        2026-09-15 实测：把示例科技的收票单挂到 101720(org104)/101721(org105)，
        `Save` 成功、`Submit` 成功（当场读到 B），**但两张单随后都变成 `D`
        （重新审核＝审核驳回）**，界面上点「查看发票」直接报：

            驳回：无法获取当前关联收票单的发票云发票流水号，请尝试删除收票单后重做收票

        根因不是"字段写错了"，而是 **发票云是按「组织税号 + 收票服务许可」授权的**：
        · 收票单 `SOURCEORGID`（购方/来源组织）**必须等于报销单组织** ——
          跨组织挂票只改写 `SETTLEORGID`，改不掉"这张票是别人的"这个事实；
          UI 拿本组织税号去发票云查别人的票 → 查不到 → 驳回。
        · 收票单还得是**发票云归集**来的（`FPDFURL` / `FPIAOZONESERIALNUMBER` 非空）；
          手工建的、没有云流水的收票单，即使同组织也会报同一个错。
        · 目标组织还必须**收票服务许可在有效期内**（示例二科技 105 实测已过期；
          发票云报 `当前使用税号【…】【收票服务】许可已过期失效…[0300]`）。
        ────────────────────────────────────────────────────────────
        """
        org_no, org_name, _ = self.bill_org_no(bill_fid, formid)
        hits, blocks, warns = [], [], []
        for no in nos:
            found = self.find_received_invoice(recv_bill_no=no)
            if not found:
                blocks.append(f"{no} 收票单不存在")
                continue
            h = found[0]
            hits.append(h)
            if str(h["src_org_no"] or "") != org_no:
                msg = (f"{no} 属于组织 {h['src_org_no']}（购方 {h['buyer']}），"
                       f"而报销单组织是 {org_no}（{org_name}）→ 跨组织，"
                       f"提交后会被驳回为 D")
                (blocks if not allow_cross_org else warns).append(msg)
            if not (h["pdf_url"] or "").strip() and not (h["serial"] or "").strip():
                msg = (f"{no} 没有发票云流水号（FPDFURL / FPIAOZONESERIALNUMBER 均为空）"
                       f"→ 不是发票云归集的收票单，界面打开会报"
                       f"「无法获取…发票云发票流水号」")
                (blocks if not allow_no_piaozone else warns).append(msg)
        return blocks, warns, hits

    def link(self, bill_fid, recv_bill_nos, formid=FORM_EXPENSE, replace=False,
             with_serial=False, skip_existing=True, allow_steal=False,
             allow_cross_org=False, allow_no_piaozone=False):
        """把收票单挂到报销单「收票信息」。

        recv_bill_nos : 收票单号列表（如 ['SPD00008518']）
        replace       : True=覆盖（Model 中未出现的行会被删除）；False=追加（默认，最安全）
        with_serial   : 是否同时写 FIVSerialNo（取收票单 FPDFURL 尾部 hash = 发票云 fid）。
                        官方流程留空，一般不需要。
        skip_existing : 已挂过的自动跳过（避免重复行）
        allow_steal   : 是否允许把「已被其它单据关联」的收票单抢过来。

        ⚠️ 抢关联是真实踩过的坑（2026-09-14）：一张收票单只能属于一张报销单，
           把它挂到新单上会**静默改掉旧单的关联**，旧单（可能已审核）会出现
           「报销单里还列着这张发票、但收票单说它属于别人」的不一致。
           因此默认 allow_steal=False，遇到已关联的收票单直接报错并列出当前归属，
           由使用者确认后显式加 --allow-steal 才继续。

        关键结构（层级放错会报「实体不存在此属性！[EntityType：BillHead]」）：
            Model = {"FID": <报销单FID>,
                     "FRecInvInfo": [{"FRecInv": {"FBillNo": "<收票单号>"}}, ...]}
        """
        if isinstance(recv_bill_nos, str):
            recv_bill_nos = [s.strip() for s in recv_bill_nos.split(",") if s.strip()]

        existing = self.list_linked(bill_fid, formid)
        existing_nos = {(row.get("RecInv") or {}).get("FBillNo") for row in existing}

        todo, skipped = [], []
        for no in recv_bill_nos:
            if skip_existing and not replace and no in existing_nos:
                skipped.append(no)
            else:
                todo.append(no)
        if skipped:
            print(f"  跳过（已挂过）：{', '.join(skipped)}")

        # ── 防抢关联：收票单若已属于别的单据，先拦下 ──
        if todo and not allow_steal:
            conflicts = []
            for no in todo:
                for h in self.find_received_invoice(recv_bill_no=no):
                    holder = h["link_bill_id"]
                    if holder and str(holder) != str(bill_fid):
                        conflicts.append(f"{no}（现属 {h['link_bill_type']} {holder} {h['link_iv']}）")
            if conflicts:
                raise SystemExit(
                    "❌ 以下收票单已被其它单据关联，继续写会把关联从原单抢走：\n     "
                    + "\n     ".join(conflicts)
                    + "\n   如确需改挂，请加 --allow-steal；若只是想把它们还给原单，"
                      "用 link <原单FID> <这些收票单号> --replace。")

        # ── 前置体检：跨组织 / 非发票云票，一律先拦下（2026-09-15 新增） ──
        if todo:
            blocks, warns, _ = self.guard_recv_invoices(
                bill_fid, todo, formid,
                allow_cross_org=allow_cross_org, allow_no_piaozone=allow_no_piaozone)
            for w in warns:
                print(f"  ⚠️ {w}")
            if blocks:
                raise SystemExit(
                    "❌ 挂票前置体检不通过（写进去 Save/Submit 都会成功，但单据在界面里不可用、"
                    "审核会被驳回）：\n     "
                    + "\n     ".join(blocks)
                    + "\n   如你已明确知道后果仍要继续，加 --allow-cross-org / "
                      "--allow-no-piaozone（两者可同时用）。")

        serial_of = {}
        if with_serial:
            for no in todo:
                hit = self.find_received_invoice(recv_bill_no=no)
                if not hit:
                    raise SystemExit(f"收票单 {no} 不存在，无法取流水号")
                m = re.search(r"/([0-9a-fA-F]{32})$", hit[0]["pdf_url"] or "")
                if m:
                    serial_of[no] = m.group(1)
                else:
                    print(f"  [!] {no} 的 FPDFURL 不是 32 位 hash 格式，跳过 FIVSerialNo")

        rows = []
        for no in todo:
            row = {"FRecInv": {"FBillNo": no}}
            if no in serial_of:
                row["FIVSerialNo"] = serial_of[no]
            rows.append(row)

        model = {"FID": bill_fid, RECV_ENTITY["save_key"]: rows}
        ok, st = self.save(formid, model, is_delete_entry=replace)
        if not ok:
            errs = st.get("Errors", [])
            raise SystemExit(f"写入失败：{json.dumps(errs[:2], ensure_ascii=False)[:500]}")
        return {"written": todo, "skipped": skipped, "response": st}

    def clear_linked(self, bill_fid, formid=FORM_EXPENSE):
        """清空报销单的全部收票信息行。

        ⚠️ 写法很反直觉，实测结论（2026-09-14）：
          · `IsDeleteEntry=true` + `FRecInvInfo: []`（空数组）        → **无效**，行还在
            （加 NeedUpDateFields=["FRecInvInfo"] 也无效）
          · `IsDeleteEntry=true` + `FRecInvInfo: [{}]`（一个空行对象）→ ✅ **清空到 0 行**
        即：要靠"一个不带 Id 的空行"去顶掉所有已有行。
        """
        before = self.list_linked(bill_fid, formid)
        if not before:
            print("  当前无收票信息行，无需清空")
            return []
        ok, st = self.save(formid, {"FID": bill_fid, RECV_ENTITY["save_key"]: [{}]},
                           is_delete_entry=True)
        if not ok:
            raise SystemExit(f"清空失败：{json.dumps(st.get('Errors', [])[:2], ensure_ascii=False)[:400]}")
        after = self.list_linked(bill_fid, formid)
        removed = [(r.get("RecInv") or {}).get("FBillNo") for r in before]
        print(f"  已清空 {len(removed)} 行：{removed}；剩余 {len(after)} 行")
        return removed


    # ── 往来单位 / 提交 ──
    # ⚠️ View 里这两个字段显示为 `CONTACTUNIT`/`CONTACTUNITTYPE`，
    #    但 **Save 键是 `FCONTACTUNIT`/`FCONTACTUNITTYPE`**（元数据 MustInput=1，均未锁）。
    #    官方单据规律：`FCONTACTUNIT` = 申请人本人（= ProposerID）。
    def fill_contact_unit(self, bill_fid, emp_no=None, emp_id=None, formid=FORM_EXPENSE):
        """补「往来单位」。需给 emp_no（员工号，如 "123"）或 emp_id（员工内码）。"""
        if not emp_no and not emp_id:
            raise ValueError("需要 emp_no（员工号）或 emp_id（员工内码）")
        ref = {"FNumber": str(emp_no)} if emp_no else {"Id": int(emp_id)}
        model = {"FID": bill_fid, "FCONTACTUNITTYPE": "BD_Empinfo", "FCONTACTUNIT": ref}
        return self.save(formid, model, need_update=["FCONTACTUNIT", "FCONTACTUNITTYPE"])

    def submit(self, bill_fid, formid=FORM_EXPENSE, selected_post_id=0):
        """提交单据（触发审批流）。注意：会真实发起审批，先跟用户确认。"""
        r = self._svc("Submit", formid, {
            "CreateOrgId": 0, "Numbers": [], "Ids": str(bill_fid),
            "SelectedPostId": selected_post_id, "UseOrgId": 0,
            "NetworkCtrl": "", "IgnoreInterationFlag": ""}, timeout=120)
        st = (r.get("Result", {}) or {}).get("ResponseStatus", {}) or {}
        return bool(st.get("IsSuccess")), st

    def precheck(self, bill_fid, formid=FORM_EXPENSE):
        """提交前体检。返回 dict：
        {status, bill_no, contact_unit, reimb_amt, recv_rows, inv_amt,
         blocks:[...], warns:[...]}
        实测：**往来单位为空也能 Submit 成功**（不是硬性校验），但单据不完整，建议补。"""
        o = self.view(formid, bill_fid)
        rows = [r for r in (o.get(RECV_ENTITY["view_name"]) or []) if isinstance(r, dict)]
        inv_amt = sum((r.get("RecInv") or {}).get("FSUMALLAMOUNT") or 0 for r in rows)
        # 报销金额字段在两类单据上同名
        reimb_amt = o.get("ExpAmountSum") or o.get("AmountSum") or 0
        cu = o.get("CONTACTUNIT") or {}
        blocks, warns = [], []
        status = o.get("DocumentStatus")
        if status != "A":
            warns.append(f"单据状态不是「暂存 A」，当前 = {status}"
                         "（B/C 也可 Submit/流转，但请确认是否重复提交）")
        if not rows:
            # ⚠️ 曾经是 blocks —— 2026-09-15 实测推翻：
            #    101710（org 105 示例二科技）**报销金额 1,309,161.65、收票信息 0 行**，
            #    照样 Submit 成功并走到 C 已审核（该组织的既有做法就是走附件）。
            #    所以"收票信息为空"不能当阻断项，否则会把 105 这类组织的正常单据误拦。
            warns.append("收票信息为空 —— 若该组织既有做法是走附件（如 org 105），这是正常的；"
                         "若该组织走收票信息路线，财务可能以「发票金额小于报销金额」驳回")
        elif inv_amt < reimb_amt:
            blocks.append(f"发票价税合计 {inv_amt} < 报销金额 {reimb_amt}")
        if not cu.get("Id"):
            warns.append("往来单位(FCONTACTUNIT)为空 —— 实测不影响 Submit，但单据不完整，建议补")

        # ── 收票单组织归属 / 发票云流水号（2026-09-15 新增）──
        # 这两条是"提交成功但界面报错、审核被驳回为 D"的真正成因，必须前置暴露。
        recv_nos = [(r.get("RecInv") or {}).get("FBillNo") for r in rows]
        recv_nos = [n for n in recv_nos if n]
        if recv_nos:
            g_blocks, g_warns, _ = self.guard_recv_invoices(bill_fid, recv_nos, formid)
            blocks.extend(g_blocks)
            warns.extend(g_warns)
        return {"status": status, "bill_no": o.get("BillNo"), "contact_unit": cu,
                "reimb_amt": reimb_amt, "recv_rows": rows, "inv_amt": inv_amt,
                "blocks": blocks, "warns": warns}

    # ────────────────────────── 命令 ──────────────────────────
def _fmt_inv(hit):
    return (f"{hit['bill_no']:16s} 发票号={hit['invoice_no']:24s} "
            f"价税合计={hit['amount']:<10} 开票日={str(hit['open_date'])[:10]} "
            f"销方={(hit['seller'] or '')[:18]}\n"
            f"      关联单据={(hit['link_bill_type'] or '').strip() or '（未关联）'} "
            f"{hit['link_bill_id']} {hit['link_iv'] or ''}")


def cmd_find(kd, a):
    # ⚠️ 实践反馈：用户常把「收票单号 SPD00008634」直接丢给 find，而 find 原本只按
    #    发票号码查 → 报「收票单池里没有这张发票」，误以为票不存在。
    #    这里自动判别：SPD 开头 / 非纯数字 → 当收票单号；全是数字 → 当发票号码。
    arg = (a.invoice_no or a.bill_no or "").strip()
    hits = []
    if arg.upper().startswith("SPD") or (arg and not arg.isdigit()):
        hits = kd.find_received_invoice(recv_bill_no=arg)
        if hits:
            print(f"（按「收票单号」查得 {len(hits)} 张）")
    if not hits and arg:
        hits = kd.find_received_invoice(invoice_no=arg)
    if not hits:
        print(f"❌ 收票单池里查不到「{arg}」（已按发票号码与收票单号各查一次）。")
        print("   可能原因：① 发票未归集到发票云（数电票通常自动归集，纸票需用「发票助手」App 采集）")
        print("             ② 号码填错  ③ 该发票属于其它组织/账套  ④ 该票已被删除（删单会释放归属）")
        return 2
    for h in hits:
        print("✅", _fmt_inv(h))
    return 0


def cmd_list(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    rows = kd.list_linked(a.fid, formid)
    print(f"报销单 {a.fid}（{formid}）收票信息：{len(rows)} 行")
    for i, row in enumerate(rows):
        r = row.get("RecInv") or {}
        print(f"  [{i+1}] 收票单={r.get('FBillNo')} 发票号={r.get('FIVNUMBER')} "
              f"价税合计={r.get('FSUMALLAMOUNT')} 开票日={str(r.get('FOPENDATE'))[:10]} "
              f"销方={r.get('FSALENAME')} 序列号={row.get('FIVSERIALNO')!r}")
    return 0


def cmd_link(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    print(f"→ 报销单 {a.fid}（{formid}）"
          f"{'覆盖' if a.replace else '追加'}写入收票单 {a.nos}")
    res = kd.link(a.fid, a.nos, formid=formid, replace=a.replace,
                  with_serial=a.with_serial, allow_steal=a.allow_steal,
                  allow_cross_org=a.allow_cross_org,
                  allow_no_piaozone=a.allow_no_piaozone)
    print(f"  ✅ 写入 {len(res['written'])} 行：{res['written']}")
    time.sleep(1)
    print("\n  回读校验：")
    cmd_list(kd, a)
    print("\n  收票单侧联动：")
    for no in res["written"]:
        for h in kd.find_received_invoice(recv_bill_no=no):
            print("   ", _fmt_inv(h))
    return 0


def cmd_clear(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    print(f"→ 清空报销单 {a.fid}（{formid}）的收票信息")
    kd.clear_linked(a.fid, formid)
    cmd_list(kd, a)
    return 0


def cmd_precheck(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    r = kd.precheck(a.fid, formid)
    print(f"报销单 {r['bill_no']} (FID {a.fid}) 提交前体检")
    print(f"  状态        : {r['status']}")
    cu = r["contact_unit"]
    print(f"  往来单位    : {cu.get('Number')} {cu.get('Name') or ''}".rstrip() or "  往来单位    : （空）")
    print(f"  报销金额    : {r['reimb_amt']}")
    print(f"  收票信息    : {len(r['recv_rows'])} 行，发票价税合计 {r['inv_amt']}")
    for w in r["warns"]:
        print(f"  ⚠️ {w}")
    for b in r["blocks"]:
        print(f"  ❌ {b}")
    print("  " + ("✅ 可以提交" if not r["blocks"] else "⛔ 有阻断项，先修再提交"))
    return 0 if not r["blocks"] else 2


def cmd_set_contact(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    print(f"→ 给报销单 {a.fid} 补往来单位：{a.emp or ('Id=' + str(a.emp_id))}")
    ok, st = kd.fill_contact_unit(a.fid, emp_no=a.emp, emp_id=a.emp_id, formid=formid)
    print("  ", "✅ 成功" if ok else "❌ 失败 " + json.dumps(st.get("Errors", [])[:2], ensure_ascii=False)[:400])
    if ok:
        cu = kd.view(formid, a.fid).get("CONTACTUNIT") or {}
        print(f"   现往来单位 = {cu.get('Number')} {cu.get('Name')}")
    return 0 if ok else 1


def cmd_submit(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    r = kd.precheck(a.fid, formid)
    if r["blocks"] and not a.force:
        print("⛔ 体检有阻断项，已中止（确认要强提交请加 --force）：")
        for b in r["blocks"]:
            print("   ❌", b)
        return 2
    for w in r["warns"]:
        print("  ⚠️", w)
    print(f"→ 提交报销单 {r['bill_no']} (FID {a.fid}) …")
    ok, st = kd.submit(a.fid, formid)
    if ok:
        print(f"  ✅ 提交成功  {json.dumps(st.get('SuccessEntitys', []), ensure_ascii=False)[:200]}")
    else:
        print(f"  ❌ 提交失败 MsgCode={st.get('MsgCode')} "
              f"{json.dumps(st.get('Errors', [])[:2], ensure_ascii=False)[:400]}")
        return 1
    time.sleep(2)
    o = kd.view(formid, a.fid)
    cu = o.get("CONTACTUNIT") or {}
    print(f"  提交后：状态={o.get('DocumentStatus')}（B=已提交） 往来单位={cu.get('Number') or '（空）'} "
          f"收票信息={len(o.get(RECV_ENTITY['view_name']) or [])} 行")
    return 0


def cmd_verify(kd, a):
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    print("① 定位收票单")
    hits = []
    for no in a.nos:
        got = kd.find_received_invoice(recv_bill_no=no)
        if not got:
            raise SystemExit(f"❌ 收票单 {no} 不存在")
        hits += got
        for h in got:
            print("   ", _fmt_inv(h))
    print("\n② 写入收票信息")
    res = kd.link(a.fid, a.nos, formid=formid, replace=a.replace,
                  with_serial=a.with_serial, allow_steal=a.allow_steal,
                  allow_cross_org=a.allow_cross_org,
                  allow_no_piaozone=a.allow_no_piaozone)
    print(f"   ✅ {res['written']}")
    time.sleep(2)
    print("\n③ 回读报销单")
    rows = kd.list_linked(a.fid, formid)
    ok = True
    got_nos = {(r.get("RecInv") or {}).get("FBillNo") for r in rows}
    for no in a.nos:
        hit = no in got_nos
        ok &= hit
        print(f"   {'✅' if hit else '❌'} {no} {'已挂上' if hit else '未挂上'}")
    print("\n④ 收票单侧回写校验（与官方插件结果的等效性证据）")
    for no in a.nos:
        for h in kd.find_received_invoice(recv_bill_no=no):
            linked = str(h["link_bill_id"]) == str(a.fid)      # 一侧是 int 一侧是 str，统一比较
            ok &= linked
            print(f"   {'✅' if linked else '❌'} {no}: LINKBILLTYPE={h['link_bill_type']!r} "
                  f"LINKBILLID={h['link_bill_id']} LINKIVNUMBER={h['link_iv']!r}")
    print(f"\n{'✅ 全链路通过' if ok else '❌ 有校验未通过'}")
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description="报销单「收票信息」写入（纯金蝶 WebAPI）")
    p.add_argument("--travel", action="store_true", help="操作差旅费报销单 ER_ExpReimbursement_Travel")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("find", help="按发票号码/收票单号查收票单")
    s.add_argument("invoice_no", nargs="?", help="发票号码")
    s.add_argument("--bill-no", help="收票单号")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("list", help="查看报销单当前收票信息")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("link", help="把收票单挂到报销单收票信息")
    s.add_argument("fid")
    s.add_argument("nos", help="收票单号，多个用逗号分隔")
    s.add_argument("--replace", action="store_true", help="覆盖模式（删除未列出的行）")
    s.add_argument("--with-serial", action="store_true", help="同时写发票云流水号 FIVSerialNo")
    s.add_argument("--allow-steal", action="store_true",
                   help="允许把已被其它单据关联的收票单抢过来（默认禁止）")
    s.add_argument("--allow-cross-org", action="store_true",
                   help="允许跨组织挂票（默认拦截：能写进去，但界面取不到发票云流水号、单据会变 D）")
    s.add_argument("--allow-no-piaozone", action="store_true",
                   help="允许挂没有发票云流水号的收票单（默认拦截：界面打开/补发票会报错）")
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser("clear", help="清空报销单全部收票信息行")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_clear)

    s = sub.add_parser("precheck", help="提交前体检（状态/往来单位/发票金额）")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_precheck)

    s = sub.add_parser("set-contact", help="补往来单位（官方规律=申请人本人）")
    s.add_argument("fid")
    s.add_argument("emp", nargs="?", help="员工号，如 123")
    s.add_argument("--emp-id", type=int, help="改用员工内码")
    s.set_defaults(fn=cmd_set_contact)

    s = sub.add_parser("submit", help="提交单据（先体检，有阻断项则中止）")
    s.add_argument("fid")
    s.add_argument("--force", action="store_true", help="忽略阻断项强提交")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("verify", help="一键自检：定位→写入→回读→联动校验")
    s.add_argument("fid")
    s.add_argument("nos", help="收票单号，多个用逗号分隔")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--with-serial", action="store_true")
    s.add_argument("--allow-steal", action="store_true")
    s.add_argument("--allow-cross-org", action="store_true")
    s.add_argument("--allow-no-piaozone", action="store_true")
    s.set_defaults(fn=cmd_verify)

    # 让 --travel 写在子命令「前」或「后」都能生效
    # （default=SUPPRESS：子命令未显式给出时不覆盖顶层已解析的值）
    for _sp in sub.choices.values():
        _sp.add_argument("--travel", action="store_true", default=argparse.SUPPRESS,
                         help="操作差旅费报销单（写在子命令前后均可）")

    a = p.parse_args()
    if getattr(a, "nos", None) and isinstance(a.nos, str):
        a.nos = [x.strip() for x in a.nos.split(",") if x.strip()]
    kd = Kingdee(load_kingdee_config())
    return a.fn(kd, a)


if __name__ == "__main__":
    sys.exit(main())
