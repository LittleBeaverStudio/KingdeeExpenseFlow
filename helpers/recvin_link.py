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
  2. **死磕 FIVSerialNo**：官方流程产物的 FIVSERIALNO 是空的（实测 100003/100004 均为 " "）。
     真正承载关联的是 `FRecInv`（收票单）。FIVSerialNo 是可选装饰，不是入口。
     ⚠️ **但这条只在「同组织」成立**（100003/100004 都是本组织的票）：
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
  ⚠️ **v2.0.11 起这三条全部是硬拦截，没有任何逃生口**（曾经的 `--allow-cross-org` /
  `--allow-no-piaozone` / `--allow-no-invoice` / `--force` 已删除）。

════════════════════════════════════════════════════════════════════════
已验证事实（示例科技生产环境，2026-09-14）
════════════════════════════════════════════════════════════════════════
· 报销单 100001 (FYBX20260101000001) 写入收票单 SPD00000001 → RecInvInfo 1 行 ✅
· 联动自动回填：110001.LINKBILLTYPE=ER_ExpReimbursement, LINKBILLID=100001,
              LINKIVNUMBER=FYBX20260101000001, LINKBILLDATE=2026-09-14 ✅
· 与官方流程产物对比（100004 ← 110002/110003）逐字段一致 ✅
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
python recvin_link.py find 24000000000000000001

# 2) 查看报销单当前收票信息
python recvin_link.py list 100001

# 3) 把收票单挂到报销单「收票信息」（追加，不删已有行）
python recvin_link.py link 100001 SPD00000001

#    覆盖模式（清掉未列出的行，用于纠错）
python recvin_link.py link 100001 SPD00000001,SPD00000003 --replace

#    差旅费报销单（--travel 放子命令前或后都可以）
python recvin_link.py --travel link 100001 SPD00000001

#    连发票云流水号一起写（可选；官方流程留空，一般不需要）
python recvin_link.py link 100001 SPD00000001 --with-serial

# 4) 一键自检：写→读→校验收票单侧联动
python recvin_link.py verify 100001 SPD00000001

配置来源（优先级）：
  1) 环境变量 KINGDEE_BASE_URL / KINGDEE_ACCTID（或 KINGDEE_ACCT_NAME）
     / KINGDEE_USERNAME / KINGDEE_PASSWORD
  2) ~/.workbuddy/kingdee/config.json（推荐：技能目录之外，升级/重装不会覆盖）
  3) kingdee-data-exporter 的 config.py 里的 KINGDEE_CONFIG（早期写法，继续兼容）
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
# ⚠️ **唯一的登录入口**（v2.0.11 起）：账号口令登录。
# 本 skill 不再提供第二条登录分支（曾试过 `LoginByAppSecret` 第三方系统登录授权，
# 已评估移除：金蝶侧的「集成用户」需要额外账号与后台配置，落地成本高于收益，
# 且会让 skill 出现两个登录口、两套配置键 —— 登录口应当只有一个，即复用
# `kingdee-data-exporter` 的配置 + `ValidateUser`）。
AUTH_SUFFIX = "Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc"
DC_SUFFIX = ("Kingdee.BOS.ServiceFacade.ServicesStub.Account.AccountService."
             "GetDataCenterList.common.kdsvc")


def _normalize_base(base_url):
    """补全 base_url：确保以 /k3cloud/ 结尾且不重复拼接。"""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.lower().endswith("/k3cloud"):
        return text + "/"
    return text + "/k3cloud/"


def _fetch_datacenters(base, retries=5, retry_wait=6):
    """拉取账套列表（免认证），返回 [{id, name}]。

    ⚠️ 出口代理会偶发瞬时故障（实测形态：空响应 / 502 Bad Gateway），
    与 `Kingdee._post` 保持一致做重试，避免把网络抖动误报成"账套不存在"。
    """
    import base64
    import gzip

    url = base + DC_SUFFIX
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(
                url, data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                raise ValueError("响应为空")
            if text.startswith("H4sI"):  # 部分环境把响应 base64+gzip 后再返回
                text = gzip.decompress(base64.b64decode(text)).decode("utf-8", "replace")
            data = json.loads(text)
            rows = data if isinstance(data, list) else (data.get("Data") or [])
            out = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                lower = {str(k).lower(): v for k, v in item.items()}
                if str(lower.get("id") or "").strip():
                    out.append({"id": str(lower["id"]).strip(),
                                "name": str(lower.get("name") or "").strip()})
            return out
        except Exception as exc:  # 网络抖动或响应异常 → 重试
            last_error = exc
            if attempt < retries - 1:
                time.sleep(retry_wait)
    raise SystemExit(
        f"拉取账套列表失败（已重试 {retries} 次）：{last_error}\n"
        f"  请求地址：{url}\n"
        "  → 检查 base_url 是否可达、是否含 /k3cloud/（内网访问需连 VPN）。"
    )


def _login_hint(text):
    """把「登录失败」拆成可执行的根因。"""
    if "未指定允许调用WebAPI接口" in text:
        return ("该账号没有被加入 WebAPI 白名单（与账号密码无关，最高频故障）。\n"
                "  让金蝶管理员操作：基础管理 → 公共设置 → 参数设置 → 基础管理 → BOS平台 → WebAPI\n"
                "  → 「允许调用WebAPI接口用户」加入取数账号 → 保存。")
    if "数据中心无法获取到" in text:
        return "账套 ID 不对。用 `python data_exporter.py --list-datacenters` 查正确 Id，或改填 acct_name。"
    if "用户名或密码错误" in text or "CheckPasswordPolicy" in text:
        return "账号或密码错，注意大小写与首尾空格。⚠️ 密码连续错约 5 次会锁号，不要反复重试。"
    if "没有权限" in text:
        return "该账号缺少对应模块权限，找管理员开通，或换一个有权限的账号。"
    return ""

FORM_EXPENSE = "ER_ExpReimbursement"           # 费用报销单
FORM_TRAVEL = "ER_ExpReimbursement_Travel"     # 差旅费报销单
FORM_RECV_INV = "IV_ReceivedInvoice"           # 收票单

# ────────────────────────────────────────────────────────────────────────────
# 🔒 政策常量（v2.0.11）
# ────────────────────────────────────────────────────────────────────────────
# 默认**空** = 一律要求「必须挂了带发票云流水号的收票单」才能提交，
# 且收票单来源组织必须与报销单组织一致。
# 若某个组织确实有「走附件、不挂收票单」的既定财务政策（历史上 org 105 曾如此），
# 由**维护者改这一行源码**显式登记组织编号 —— 不提供任何命令行开关或运行时逃生口，
# 确保"能绕过去"这件事必须经过一次代码评审。
ATTACHMENT_ONLY_ORGS = ()          # 例：("105",) —— 默认空，即不允许任何组织走附件路线

# 两类报销单的收票信息实体（Save Key / View EntryName）——结构相同
RECV_ENTITY = {"save_key": "FRecInvInfo", "view_name": "RecInvInfo"}


def _exporter_candidates():
    """可能存放 kingdee-data-exporter 的目录（用于复用其 config.py）。"""
    here = os.path.dirname(os.path.abspath(__file__))            # .../kingdee-expense-flow/helpers
    skill_dir = os.path.dirname(here)                            # .../kingdee-expense-flow
    home_skills = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
    return (
        os.path.join(home_skills, "kingdee-data-exporter"),
        os.path.join(home_skills, "KingdeeDataExporter"),
        os.path.join(os.path.dirname(skill_dir), "kingdee-data-exporter"),
        os.path.join(os.path.dirname(skill_dir), "KingdeeDataExporter"),
        os.path.join(skill_dir, "..", "KingdeeDataExporter"),
    )


def _user_config_path():
    """技能目录之外的推荐配置位置（与 kingdee-data-exporter 共用）。"""
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "kingdee", "config.json")


def _read_user_config():
    """读 ~/.workbuddy/kingdee/config.json；支持扁平写法与 profiles 结构。"""
    path = _user_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    profiles = data.get("profiles")
    if isinstance(profiles, dict) and profiles:
        want = os.environ.get("KINGDEE_PROFILE") or str(data.get("default") or "")
        if not want:
            want = next(iter(profiles))
        picked = profiles.get(want)
        if not isinstance(picked, dict):
            return None
        common = {k: v for k, v in data.items() if k not in ("profiles", "default")}
        return {**common, **picked}
    return data


def load_kingdee_config():
    """加载金蝶连接配置。

    优先级：环境变量 > ~/.workbuddy/kingdee/config.json（推荐，技能目录之外）
            > kingdee-data-exporter 的 config.py（早期写法）
    """
    cfg = {
        "base_url": os.environ.get("KINGDEE_BASE_URL", ""),
        "acctid": os.environ.get("KINGDEE_ACCTID", ""),
        "acct_name": os.environ.get("KINGDEE_ACCT_NAME", ""),
        "username": os.environ.get("KINGDEE_USERNAME", ""),
        "password": os.environ.get("KINGDEE_PASSWORD", ""),
        "lcid": int(os.environ.get("KINGDEE_LCID", 2052)),
    }
    if cfg["base_url"] and cfg["username"] and cfg["password"]:
        return cfg

    user_cfg = _read_user_config()
    if user_cfg and str(user_cfg.get("base_url") or "").strip():
        merged = {k: v for k, v in user_cfg.items() if not isinstance(v, (dict, list))}
        return {**cfg, **{k: v for k, v in merged.items() if v}}

    for cand in _exporter_candidates():
        if os.path.exists(os.path.join(cand, "config.py")):
            sys.path.insert(0, cand)
            try:
                from config import KINGDEE_CONFIG as KC
                return {**cfg, **{k: v for k, v in KC.items() if v}}
            except Exception:
                pass

    raise SystemExit(
        "未找到金蝶连接配置。任选一种方式配置：\n"
        "\n"
        "  方式一（推荐，技能升级/重装不会覆盖）：写 ~/.workbuddy/kingdee/config.json\n"
        '    {"base_url": "https://你的域名/k3cloud/", "acct_name": "账套名称",\n'
        '     "username": "取数账号", "password": "密码"}\n'
        "    账套 ID 不用自己找——只填 acct_name 会自动解析，或跑下面第 3 步列出全部账套。\n"
        "\n"
        "  方式二（环境变量，不落盘）：\n"
        "    KINGDEE_BASE_URL / KINGDEE_ACCTID（或 KINGDEE_ACCT_NAME）\n"
        "    / KINGDEE_USERNAME / KINGDEE_PASSWORD\n"
        "\n"
        "  方式三：安装并配置「金蝶云星空数据导出」技能（kingdee-data-exporter），本技能会复用它的 config.py。\n"
        "\n"
        "配完请自检（会逐步定位是配置、网络、账套、登录还是权限问题）：\n"
        "  cd <kingdee-data-exporter 目录> && python data_exporter.py --doctor\n"
        "列出服务器上的全部账套：\n"
        "  python data_exporter.py --list-datacenters\n"
        "⚠️ 密码连续错约 5 次会锁账号，不要反复重试。"
    )


class Kingdee:
    """极简金蝶 WebAPI 客户端（登录 / 查询 / 查看 / 保存），仅用标准库。

    ⚠️ 必须带 CookieJar：金蝶登录后靠会话 Cookie 认身份，用裸 urlopen 会拿到
       「会话信息已丢失，请重新登录」（MsgCode 1），且 View 会返回空字典、不报错。
    """

    def __init__(self, cfg):
        self.base = _normalize_base(cfg["base_url"])
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

    def _resolve_acctid(self):
        """acctid 为空时按 acct_name 自动解析（GetDataCenterList 免认证，登录前可用）。"""
        acctid = str(self.cfg.get("acctid") or "").strip()
        if acctid:
            return acctid
        name = str(self.cfg.get("acct_name") or "").strip()
        if not name:
            raise SystemExit(
                "未配置账套：请在配置里填 acctid，或填 acct_name（账套名称）由脚本自动解析。\n"
                "  列出服务器上的全部账套（免账号密码）：python data_exporter.py --list-datacenters"
            )
        centers = _fetch_datacenters(self.base)
        hits = [c for c in centers if c["name"] == name] or [c for c in centers if name in c["name"]]
        if len(hits) == 1:
            print(f"  按账套名称「{name}」解析到 acctid={hits[0]['id']}")
            self.cfg["acctid"] = hits[0]["id"]
            return hits[0]["id"]
        if len(hits) > 1:
            shown = "、".join(f"{c['name']}({c['id']})" for c in hits)
            raise SystemExit(f"账套名称「{name}」匹配到多个：{shown}。请写完整名称，或直接填 acctid。")
        available = "、".join(c["name"] for c in centers[:10]) or "（服务器未返回账套列表）"
        raise SystemExit(f"账套名称「{name}」不在该服务器的账套列表里。可用账套：{available}")

    def _login(self):
        acctid = self._resolve_acctid()
        r = self._post(AUTH_SUFFIX, {
            "acctid": acctid, "username": self.cfg["username"],
            "password": self.cfg["password"], "lcid": self.cfg.get("lcid", 2052)}, timeout=30)
        if r.get("LoginResultType") != 1:
            raw = json.dumps(r, ensure_ascii=False)
            message = f"金蝶登录失败：{raw[:300]}"
            hint = _login_hint(str(r.get("Message") or "") + raw)
            if hint:
                message += f"\n  → {hint}"
            else:
                message += ("\n  → 先跑自检逐步定位：在 kingdee-data-exporter 目录执行 "
                            "`python data_exporter.py --doctor`")
            raise SystemExit(message)

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
             retries=4, retry_wait=8, validate_flag=False):
        """标准 Save。返回 (是否成功, ResponseStatus)。

        ⚠️⚠️ **`validate_flag=False`（默认）会关掉金蝶的业务校验 —— 包括
        「发票金额不允许小于报销金额！」**。2026-09-22 实测（同一张**不挂任何发票**的差旅报销单）：

        | 报文 | 结果 |
        |---|---|
        | `ValidateFlag=true`  | ❌ `MsgCode=11`，Errors 含 **「发票金额不允许小于报销金额！」** |
        | `ValidateFlag=false` | ✅ `IsSuccess=true`，单据正常生成（CLFBX…），随后 Submit 也能到 `B` |

        → 也就是说：**UI 上会被拦的「无发票 / 发票不足」单据，用本 skill 的默认参数能写进去。**
        这是能力，也是**风险**：等于造了一张 UI 造不出、后续审核/财务大概率退回的单。
        写入前想知道「UI 会不会接受」，先用 `strict_probe()`（CLI：`strict <FID>`）探一次。

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
                "ValidateFlag": "true" if validate_flag else "false",
                "NumberSearch": "true", "IsAutoAdjustField": "true",
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

    def guard_recv_invoices(self, bill_fid, nos, formid=FORM_EXPENSE):
        """挂票**前置体检**（2026-09-15 新增，血泪教训；v2.0.11 起为硬闸门）。

        返回 (blocks, warns, rows)：blocks 非空就不该写。

        🔒 **v2.0.11：三道判据全部硬拦截，无逃生口。** 命中即进 `blocks`：
          1. 收票单查不到（收票单不存在，或当前账号看不到 —— 见下方「可见性」警告）
          2. `SOURCEORGID` ≠ 报销单组织（跨组织）
          3. **`FPIAOZONESERIALNUMBER` 为空** —— 即「发票没经发票云采集/归集，
             等于没真正上传过发票」。这是"不许提交没上传发票的单"在收票单层的落点。

        ── 为什么必须查这个 ──────────────────────────────────────────
        2026-09-15 实测：把示例科技的收票单挂到 100005(org104)/100006(org105)，
        `Save` 成功、`Submit` 成功（当场读到 B），**但两张单随后都变成 `D`
        （重新审核＝审核驳回）**，界面上点「查看发票」直接报：

            驳回：无法获取当前关联收票单的发票云发票流水号，请尝试删除收票单后重做收票

        根因不是"字段写错了"，而是 **发票云是按「组织税号 + 收票服务许可」授权的**：
        · 收票单 `SOURCEORGID`（购方/来源组织）**必须等于报销单组织** ——
          跨组织挂票只改写 `SETTLEORGID`，改不掉"这张票是别人的"这个事实；
          UI 拿本组织税号去发票云查别人的票 → 查不到 → 驳回。
        · 收票单必须带**发票云流水号** `FPIAOZONESERIALNUMBER`。
          2026-09-22 补充证据：手工建的票**永远拿不到流水号**（挂单不补、事后从电子税务局
          重新下载也不补，用户实测）→ 所以"无流水号"这条路没有任何补救余地，必须硬拒。
        · 目标组织还必须**收票服务许可在有效期内**（示例二科技 105 实测已过期；
          发票云报 `当前使用税号【…】【收票服务】许可已过期失效…[0300]`）。
        ⚠️ **注意 `find_received_invoice()` 受「可见性」限制**（2026-09-22 实测）：
        可见性按**来源组织**（`FSOURCEORGID`）切 —— 员工账号只看得到本组织的票
        （取数账号 2000 张横跨 101/104/105；钱八关掉"只能查看自己单据"后也只看到 101 的）。
        用员工账号跑本方法会把"别的组织的票"误判成「收票单不存在」→ **假阴性**。
        **体检/挂票请用有权限的账号**（员工身份留给最终 Submit）。
        ────────────────────────────────────────────────────────────
        """
        org_no, org_name, _ = self.bill_org_no(bill_fid, formid)
        hits, blocks, warns = [], [], []
        for no in nos:
            found = self.find_received_invoice(recv_bill_no=no)
            if not found:
                # ⚠️ 2026-09-22：收票池可见性按「来源组织」切，所以"查不到"很可能是
                # **当前账号看不到**，而不是这张票真的不存在。
                # 该分支没有逃生口，会硬阻断；排查时先换有权限的账号重跑。
                blocks.append(
                    f"{no} 收票单不存在（或当前账号 {self.cfg.get('username')!r} 看不到它 —— "
                    f"收票池可见性按「来源组织」切，普通员工只能看到本组织的票；"
                    f"请换有权限的账号重跑，再判断是否真的不存在）")
                continue
            h = found[0]
            hits.append(h)
            if str(h["src_org_no"] or "") != org_no:
                blocks.append(
                    f"{no} 属于组织 {h['src_org_no']}（购方 {h['buyer']}），"
                    f"而报销单组织是 {org_no}（{org_name}）→ 跨组织挂票，"
                    f"提交成功也会被驳回为 D（🔒 硬拦截，无逃生口）")
            if not str(h["serial"] or "").strip():
                blocks.append(
                    f"{no} **没有发票云流水号**（FPIAOZONESERIALNUMBER 为空）→ 这张发票"
                    f"没有真正经过发票云采集，等于「没上传发票」。"
                    f"提交后界面打不开、审核会驳回为 D，且没有补救余地"
                    f"（手工建票补不回来，事后从电子税务局重新下载也补不回来）。"
                    f"🔒 硬拦截，无逃生口 —— 请让员工在软件/发票云里把这张票上传一次拿到流水号。")
        return blocks, warns, hits

    def link(self, bill_fid, recv_bill_nos, formid=FORM_EXPENSE, replace=False,
             with_serial=False, skip_existing=True, allow_steal=False):
        """把收票单挂到报销单「收票信息」。

        recv_bill_nos : 收票单号列表（如 ['SPD00000001']）
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

        # ── 前置体检：跨组织 / 无发票云流水号，一律硬拦（v2.0.11：无逃生口） ──
        if todo:
            blocks, warns, _ = self.guard_recv_invoices(bill_fid, todo, formid)
            for w in warns:
                print(f"  ⚠️ {w}")
            if blocks:
                raise SystemExit(
                    "❌ 挂票前置体检不通过（写进去 Save/Submit 都会成功，但单据在界面里不可用、"
                    "审核会被驳回）：\n     "
                    + "\n     ".join(blocks)
                    + "\n   🔒 v2.0.11 起这是硬拦截，**没有逃生口**。"
                      "请换成「同组织 + 有发票云流水号」的收票单再挂。")

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

    # ── 权威票解析 / 智能建票（2026-09-22 新增；`wait_serial` 已作废，见其 docstring）──
    def resolve_authoritative(self, invoice_no, top=20):
        """按发票号在全池解析「该用哪张收票单」（同一发票号可能有多条）。

        排序（权威优先）：
          1. 有发票云流水号 **且** 未被其它单据占用  → 直接用
          2. 有流水号 但已被占用                     → 要 `--allow-steal` 才能改挂
          3. 无流水号 且 未占用（手工建票）          → ⚠️ **不可用**（v2.0.11 起闸门会硬拒）
          4. 无流水号 且 已被占用                    → 最后

        ⚠️ **第 3 类不再是"可用"选项**（2026-09-22 结论已推翻）：手工建的票**不会补流水号**
        —— 挂到同组织报销单不补，事后从电子税务局重新下载也不补（用户实测）。
        `rank` 仍把它排在最后，只是为了**识别已存在的无号票**（历史遗留单），
        不要拿它当兜底路径；`submit` 的闸门会以「没有发票云流水号」直接拒绝。

        ⚠️ **必须用有权限的账号调用**。可见性是**按来源组织（`FSOURCEORGID`）**切的：
        员工账号只看得到**本组织**的票（实测钱八关掉"只能查看自己单据"后能看到组织 101 的
        18 个制单人，但看不到组织 104 的票）→ 否则会把"别的组织的票"误判成"不存在"。

        返回 dict: {hit, all, reason}
        """
        hits = self.find_received_invoice(invoice_no=invoice_no, top=top)
        if not hits:
            return {"hit": None, "all": [],
                    "reason": "池中无此发票号（未归集；或当前账号无权看到 → 换有权限账号重查）"}

        def rank(h):
            has = 0 if str(h.get("serial") or "").strip() else 1
            used = 1 if h.get("link_bill_id") else 0
            return (has, used)
        ordered = sorted(hits, key=rank)
        hit = ordered[0]
        has = bool(str(hit.get("serial") or "").strip())
        used = hit.get("link_bill_id")
        if has and not used:
            reason = "权威票：有发票云流水号、未被占用"
        elif has and used:
            reason = "有流水号但已被 %s 占用（改挂需 allow_steal）" % (hit.get("link_iv") or used)
        elif not has and not used:
            reason = ("⚠️ 无流水号、未占用（手工建票）—— **不会补号**（挂单不补、事后从电子税务局"
                      "重新下载也不补，2026-09-22 用户实测）→ **不建议使用**；"
                      "正确做法是等次日归集/采集后再挂")
        else:
            reason = "无流水号且已被其它单据占用"
        return {"hit": hit, "all": ordered, "reason": reason}

    def create_recv_invoice(self, invoice_no, amount, tax_amount=0.0, open_date=None,
                            seller_name="", seller_tax="", buyer_name="", buyer_tax="",
                            org_no="101", inv_type="26", item_name="", tax_rate=0.0,
                            settle_org_no=None, check_dup=True, remark=""):
        """手工建一张收票单。返回 `(bill_no, fid, created:bool, note)`。

        🔴 **默认查重**（`check_dup=True`）：金蝶**不校验发票号重复**（实测同号能建第二张、
        静默污染收票池），所以这里先 `resolve_authoritative()`，已存在就**直接返回那张**，
        不再新建 —— 这是防重与防污染的**唯一防线**。

        🔴 **手工建的票永远拿不到发票云流水号**（`GENERATETYPE=' '`、
        `FPIAOZONESERIALNUMBER=' '`、`ISEXAMINE='0'`）：挂到同组织报销单**不补**，
        事后从电子税务局重新下载**也不补**（2026-09-22 用户实测）。
        ⛔ 曾经据 `SPD00008775`「09-21 无号 → 09-22 16:14 有号」推断的"挂单后异步补号"
        **已作废** —— 复核发现它是**被发票云重新采集覆盖**（`GENERATETYPE` 由 `' '` 变 `'3'`
        并出现 `FPDFURL`），不是回填。

        ⇒ 所以本方法现在**只用于测试**（造票验证字段/挂票机制）或**历史遗留票补录**，用完即删。
        **v2.0.11 起它建出来的票已经"提交不了"**：`submit` 的闸门会以「没有发票云流水号」硬拒。
        它丢的是三重财务保护：①**防重**（发票云按 `expenseStatus` 锁票，手工票完全没有，
        金蝶也不查同号 → 同一张票可报两次）②**可信**（税局源数据 vs OCR 猜测）
        ③**验真**（`ISEXAMINE=0`，无查验记录、无原件 URL）。
        """
        if check_dup:
            got = self.resolve_authoritative(invoice_no)
            if got["hit"]:
                h = got["hit"]
                return h["bill_no"], h["fid"], False, \
                    "已存在 %s → 不重复建（%s）" % (h["bill_no"], got["reason"])
        net = round(float(amount) - float(tax_amount or 0), 2)
        model = {
            "FIVNUMBER": invoice_no, "FIVCODE": "",
            "FOPENDATE": str(open_date or "")[:10],
            "FSUMAMOUNT": net, "FSUMTAXAMOUNT": float(tax_amount or 0),
            "FSUMALLAMOUNT": float(amount),
            "FPURNAME": buyer_name, "FPURTAXNUMBER": buyer_tax,
            "FSALENAME": seller_name, "FSALETAXNUMBER": seller_tax,
            "FINVOICETYPE": str(inv_type or "26"),
            "FISELECTRONIC": "true", "FSTATUS": "0", "FRemark": remark,
            "FSOURCEORGID": {"FNumber": str(org_no)},
            "FSETTLEORGID": {"FNumber": str(settle_org_no or org_no)},
        }
        if item_name:
            model["FEntity"] = [{
                "FITEMNAME": item_name, "FUNIT": "", "FSPECIFICATIONS": "",
                "FQTY": 1.0, "FPRICE": net, "FAMOUNT": net,
                "FTAXRATE": float(tax_rate or 0), "FTAXAMOUNT": float(tax_amount or 0),
                "FTOTALAMOUNT": float(amount)}]
        ok, st = self.save(FORM_RECV_INV, model)
        if not ok:
            raise SystemExit("建收票单失败：%s"
                             % json.dumps(st.get("Errors", [])[:2], ensure_ascii=False)[:400])
        for e in (st.get("SuccessEntitys") or []):
            return e.get("Number"), e.get("Id"), True, "新建成功"
        return None, None, True, "Save 成功但未返回单号（检查 SuccessEntitys 结构）"

    def wait_serial(self, recv_bill_no, timeout=7200, interval=120, verbose=True):
        """轮询等待某张收票单的**发票云流水号被异步回写**。返回 `(serial, waited, polls)`。

        ⚠️ **本函数的立论已被推翻，仅作历史保留**（2026-09-22）：
        当初据 `SPD00008775`（09-21 无号 → 09-22 16:14 有号）推断"挂单后异步补号"，
        复核发现它是**被发票云重新采集覆盖**（`GENERATETYPE` 由 `' '` 变 `'3'`、并出现
        `FPDFURL`），不是"补号"；且用户实测**手工建票事后从电子税务局重新下载仍无号**。
        → 所以**不要再用它等号**；拿不到号就该走"次日归集后再挂"。
        保留原因：若将来出现"票已由发票云采集、但号还没同步下来"的真实场景，它仍可用。
        超时返回空串（不算错误，只是"还没等到"）。
        """
        t0 = time.time()
        polls = 0
        while True:
            polls += 1
            hits = self.find_received_invoice(recv_bill_no=recv_bill_no)
            ser = str((hits[0].get("serial") if hits else "") or "").strip()
            if ser:
                return ser, int(time.time() - t0), polls
            waited = time.time() - t0
            if waited >= timeout:
                return "", int(waited), polls
            if verbose:
                print("  … 等流水号回写（已等 %d 秒 / 上限 %d 秒）" % (int(waited), timeout))
            time.sleep(max(1, min(interval, timeout - waited)))

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
        """提交单据（触发审批流）。注意：会真实发起审批，先跟用户确认。

        🔒 **闸门是强制的，没有任何参数可以关掉它**（v2.0.11 起）。
        提交前跑三道检查，任一道不通过就**拒绝提交**并返回 `MsgCode="GUARD"`：

        1. **金蝶自己的严格校验**（`strict_probe`，即 `ValidateFlag=true`）——
           用金蝶的规则判，口径不会跑偏；命中「发票金额不允许小于报销金额！」直接拒。
        2. **skill 自算口径**（`precheck`）—— 补第 1 道的盲区：若单据还有别的必填缺失，
           金蝶可能在校验到发票金额**之前**就返回失败，探针的 `strict_errors` 会是空。
        3. **发票实质检查**（同在 `precheck` 内）：收票信息 0 行 → 拒；
           挂的收票单缺发票云流水号 → 拒；跨组织票 → 拒。

        背景（2026-09-22 实测）：本 skill 的 `save()` 默认 `ValidateFlag=false`，会把
        「发票金额不允许小于报销金额！」**整条关掉** —— 同一张 0 发票的差旅报销单，
        `true` 报 `MsgCode=11` 被拦、`false` 却 `IsSuccess=true` 且 `Submit` 也能推到状态 B。
        这道闸门就是为了堵住"UI 拦得住、却进了审批流"的单。

        ⚠️ **历史逃生口已删除**（`allow_no_invoice` / `allow_overspend` / CLI `--force`）。
        原因：提交进审批流后 WebAPI 撤不回来（`Delete` 只放 Z/A/D、`UnAudit` 被工作流挡），
        "能绕过闸门"本身就是最大的风险点。若确实存在业务上必须放行的场景，
        应当由维护者评估后改源码（例如把组织登记进 `ATTACHMENT_ONLY_ORGS`），
        而不是留一个运行时开关。
        """  # noqa: E501
        probe = self.strict_probe(bill_fid, formid)
        if probe.get("strict_errors"):
            msg = ("🔒 闸门拦下提交：金蝶严格校验(ValidateFlag=true)判定该单不合规 → "
                   + "；".join(probe["strict_errors"])[:300]
                   + "。金蝶在这条 WebAPI 路径上默认会放过(ValidateFlag=false)，"
                     "但 UI / 审核不会。"
                     "👉 合规路径：`expense_edit.py fit <fid>` 把报销金额调到发票金额。")
            print("  " + msg)
            return False, {"MsgCode": "GUARD", "IsSuccess": False,
                           "Errors": [{"Message": msg}]}
        # 探针顺带能看出"还缺哪些必填" —— 只提示、不阻断（提交本身不校验必填）
        missing = [m for m in (probe.get("errors") or [])
                   if "必填" in m or "必录" in m]
        if missing:
            print("  ⚠️ 金蝶严格校验提示，该单还有必填项未填（不影响本次提交判定）：")
            for m in missing[:4]:
                print("     •", str(m)[:160])
        pc = self.precheck(bill_fid, formid)
        if pc["blocks"]:
            msg = "🔒 闸门拦下提交：" + "；".join(pc["blocks"])[:400]
            print("  " + msg)
            return False, {"MsgCode": "GUARD", "IsSuccess": False,
                           "Errors": [{"Message": msg}]}
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

        🔒 **本方法就是闸门本体，判据全部硬编码，没有任何放行参数**（v2.0.11）：
          · **收票信息 0 行** → 拒（= 不允许提交「没有上传发票」的报销单）
          · **发票价税合计 < 报销金额** → 拒（金蝶的这条校验被 `ValidateFlag=false` 关掉了，
            所以必须自己算）
          · **关联的收票单没有发票云流水号** → 拒（= 不允许提交「发票没真正上传」的单）
          · **关联的收票单与报销单不同组织** → 拒（跨组织必被驳回为 D）
        唯一的组织级例外是 `ATTACHMENT_ONLY_ORGS` —— 默认空；确需「走附件不挂票」的组织
        由维护者改源码登记，不提供运行时开关。

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
        _org_no = str(self.bill_org_no(bill_fid, formid)[0] or "").strip()
        _attachment_ok = _org_no in ATTACHMENT_ONLY_ORGS
        if not rows:
            if _attachment_ok:
                warns.append(
                    f"收票信息为空 —— 组织 {_org_no} 已在 ATTACHMENT_ONLY_ORGS 里登记为"
                    "「走附件不挂票」政策，放行（这是源码级例外，不是运行时开关）")
            else:
                # 🔒 v2.0.11 硬拦截：没上传发票 = 不允许提交
                blocks.append(
                    f"🔒 **收票信息为空**（本单报销金额 {reimb_amt}）—— "
                    "不允许提交没有上传发票的报销单。"
                    "收票信息为空的单进审批流后，界面打不开发票、审核会驳回为 D，"
                    "而且提交后 WebAPI 撤不回来（只能人工在 UI 驳回）。"
                    "请先把员工的发票挂到「收票信息」再提交；"
                    "本闸门无运行时开关（组织级例外只能由维护者改 "
                    "`ATTACHMENT_ONLY_ORGS` 源码登记）。")
        elif inv_amt < reimb_amt:
            blocks.append(
                f"发票价税合计 {inv_amt} < 报销金额 {reimb_amt}，差额 {round(reimb_amt - inv_amt, 2)}"
                f" —— 先把报销金额调到发票金额再提交（合规做法，用 "
                f"`expense_edit.py fit <fid>`，默认只算不写、加 --apply 才写）。"
                f"没有绕过参数：绕过去会造出审核必退的单。")
        if not cu.get("Id"):
            warns.append("往来单位(FCONTACTUNIT)为空 —— 实测不影响 Submit，但单据不完整，建议补")

        # ── 收票单组织归属 / 发票云流水号（2026-09-15 新增，v2.0.11 起硬拦截）──
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

    def strict_probe(self, bill_fid, formid=FORM_EXPENSE):
        """**严格模式探针**：用 `ValidateFlag=true` 对单据做一次「空写入」，
        把金蝶的业务校验错误原样抛出来 —— 用来回答"**这份单据 UI/审核会不会接受**"。

        背景（2026-09-22 实测）：本 skill 的 `save()` 默认 `ValidateFlag=false`，
        **会把「发票金额不允许小于报销金额！」这条校验一起关掉** ——
        于是能写出 UI 拦得住的单据。写完想知道"UI 会不会拦"，就调本方法探一次。

        返回 dict：{ok, msg_code, errors:[...], strict_errors:[...]}
        - `errors` 里若出现「发票金额不允许小于报销金额」→ 该单在 UI 里过不了。
        - 同时会带出其它必填项（往来单位 / 差旅费类型 `FTravelType` …），这些是 UI 也会要求的，
          属于「单据本身没填完整」，不是发票问题，读的时候要分开看。

        📌 2026-09-22 实测（回答"手工建的无流水号票能不能用"）：
        把一张手工建的收票单（`GENERATETYPE=' '`、`FPIAOZONESERIALNUMBER=' '`、同组织 101）
        挂到同组织差旅报销单上，明细金额对齐后跑本探针 →
        - 「发票金额不允许小于报销金额！」**消失**（金额没对齐时会报，这是**唯一**发票类校验）；
        - **没有任何「发票云流水号」相关报错** → **流水号不参与 Save 阶段校验**。
        另注：差旅报销单明细改金额时，`FExpTravelAmount`(差旅费金额) 要与
        `FTaxSubmitAmt`(税额) + 费用金额 保持勾稽，否则报「差旅费金额不等于税额加费用金额」。

        ⚠️ 这是**只读性质的空写入**（`NeedUpDateFields=[]` + 仅 `FID`），不会改字段值；
        但因为 `ValidateFlag=true`，**若单据本身违反必填约束会返回失败** —— 这是预期的，不是 bug。

        ⚠️ 已知盲区（2026-09-22 实测）：**"编辑锁冲突"会把这个探针打断** ——
        报 `MsgCode=4`「XXX使用业务单据…冲突，请稍候再使用」，此时 `errors` 里
        **根本没有发票金额那一句**，`strict_errors` 为空。若拿它当唯一闸门，
        就会在锁冲突时**静默放行**。更麻烦的是**同一账号「先 View 再 Save 探针」会自锁**
        （2026-09-22 实测重试 2 次仍冲突），所以本方法只重试 1 次就放弃；
        并且 `submit()` 的闸门**绝不能只靠它** —— 必须再叠一层自算口径（`precheck`），
        越是拿不到金蝶结论，越要靠自己算。
        """
        st = {}
        for attempt in range(2):
            r = self._svc("Save", formid, {
                "NeedUpDateFields": [], "NeedReturnFields": [],
                "IsDeleteEntry": "false", "SubSystemId": "",
                "IsVerifyBaseDataField": "true", "IsEntryBatchFill": "true",
                "ValidateFlag": "true", "NumberSearch": "true", "IsAutoAdjustField": "true",
                "InterationFlags": "", "IgnoreInterationFlag": "true",
                "IsControlPrecision": "false", "ValidateRepeatJson": "true",
                "Model": {"FID": bill_fid}}, timeout=120)
            st = (r.get("Result", {}) or {}).get("ResponseStatus", {}) or {}
            msgs = [str(e.get("Message") or "") for e in (st.get("Errors") or [])]
            blob = json.dumps(msgs, ensure_ascii=False)
            if "冲突" not in blob and "请稍候再使用" not in blob:
                break
            if attempt == 0:
                print("  [!] 严格探针遇编辑锁冲突，等 6s 重试 1 次…")
                time.sleep(6)
        msgs = [str(e.get("Message") or "") for e in (st.get("Errors") or [])]
        inv_err = [m for m in msgs if "发票金额" in m]
        return {"ok": bool(st.get("IsSuccess")), "msg_code": st.get("MsgCode"),
                "errors": msgs, "strict_errors": inv_err}

    # ────────────────────────── 命令 ──────────────────────────
def _fmt_inv(hit):
    return (f"{hit['bill_no']:16s} 发票号={hit['invoice_no']:24s} "
            f"价税合计={hit['amount']:<10} 开票日={str(hit['open_date'])[:10]} "
            f"销方={(hit['seller'] or '')[:18]}\n"
            f"      关联单据={(hit['link_bill_type'] or '').strip() or '（未关联）'} "
            f"{hit['link_bill_id']} {hit['link_iv'] or ''}")


def cmd_find(kd, a):
    # ⚠️ 实践反馈：用户常把「收票单号 SPD00000002」直接丢给 find，而 find 原本只按
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
                  with_serial=a.with_serial, allow_steal=a.allow_steal)
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


def cmd_resolve(kd, a):
    """按发票号解析「该用哪张收票单」——同号多条时选权威票（有流水号 + 未占用）。"""
    r = kd.resolve_authoritative(a.invoice_no)
    print("发票号 %s → 池中 %d 条" % (a.invoice_no, len(r["all"])))
    for h in r["all"]:
        print("   %-13s FID=%-8s 金额=%-10s 流水号=%-6s 组织=%s 挂单=%s" % (
            h["bill_no"], h["fid"], h["amount"],
            "有" if str(h["serial"] or "").strip() else "【空】",
            h["src_org_no"], h["link_iv"] or "（未占用）"))
    print()
    if r["hit"]:
        print("   ✅ 建议使用：%s (FID %s)" % (r["hit"]["bill_no"], r["hit"]["fid"]))
        print("      理由：%s" % r["reason"])
        return 0
    print("   ❌ %s" % r["reason"])
    return 2


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


def cmd_strict(kd, a):
    """严格模式探针：ValidateFlag=true 空写入，把金蝶业务校验错误原样抛出。"""
    formid = FORM_TRAVEL if a.travel else FORM_EXPENSE
    o = kd.view(formid, a.fid)
    recv = [r for r in (o.get(RECV_ENTITY["view_name"]) or []) if isinstance(r, dict)]
    inv = 0.0
    for r in recv:
        try:
            inv += float((r.get("RecInv") or {}).get("FSUMALLAMOUNT") or 0)
        except Exception:
            pass
    print(f"严格模式探针 {o.get('BillNo')} (FID {a.fid})  —— 模拟 UI/审核的业务校验")
    print(f"  状态        : {o.get('DocumentStatus')}")
    print(f"  报销金额    : {o.get('ExpAmountSum')}")
    print(f"  收票信息    : {len(recv)} 行，发票价税合计 {inv}")
    r = kd.strict_probe(a.fid, formid)
    print(f"  ValidateFlag=true 写入结果: IsSuccess={r['ok']}  MsgCode={r['msg_code']}")
    if r["ok"]:
        print("  ✅ 金蝶业务校验通过 —— 这份单据 UI/审核也会接受")
        return 0
    for m in r["errors"]:
        flag = "🔴" if "发票金额" in m else "•"
        print(f"    {flag} {m}")
    if r["strict_errors"]:
        print()
        print("  ⛔ 命中「发票金额不允许小于报销金额」→ 该单在 UI 里过不了（本 skill 默认参数能写进去，是越狱）")
        return 2
    print()
    print("  ℹ️ 没有发票金额类错误，上面的报错都是「必填项没填完整」（UI 也会要求），"
          "补完这些字段后再探一次")
    return 1


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
    if r["blocks"]:
        print("⛔ 体检有阻断项，已中止 —— 🔒 闸门没有绕过开关：")
        for b in r["blocks"]:
            print("   ❌", b)
        return 2
    for w in r["warns"]:
        print("  ⚠️", w)
    print(f"→ 提交报销单 {r['bill_no']} (FID {a.fid}) …")
    ok, st = kd.submit(a.fid, formid)
    if ok:
        print(f"  ✅ 提交成功  {json.dumps(st.get('SuccessEntitys', []), ensure_ascii=False)[:200]}")
    elif st.get("MsgCode") == "GUARD":
        print("  🔒 提交被闸门拦下（未发出 Submit 请求）。")
        print("     闸门没有逃生口：请按上面的提示把单据修合规（补挂带发票云流水号的收票单 / "
              "把报销金额调到发票金额）后重试。")
        return 3
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
                  with_serial=a.with_serial, allow_steal=a.allow_steal)
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
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser("clear", help="清空报销单全部收票信息行")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_clear)

    s = sub.add_parser("precheck", help="提交前体检（🔒 闸门本体：无票/无流水号/跨组织/金额，全部硬拦）")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_precheck)

    s = sub.add_parser("resolve", help="按发票号解析「该用哪张收票单」（同号多条时选权威票）")
    s.add_argument("invoice_no", help="发票号码")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("strict", help="严格模式探针：用 ValidateFlag=true 探测金蝶业务校验会不会拦")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_strict)

    s = sub.add_parser("set-contact", help="补往来单位（官方规律=申请人本人）")
    s.add_argument("fid")
    s.add_argument("emp", nargs="?", help="员工号，如 123")
    s.add_argument("--emp-id", type=int, help="改用员工内码")
    s.set_defaults(fn=cmd_set_contact)

    s = sub.add_parser("submit", help="提交单据（🔒 强制闸门：无票 / 无流水号 / 跨组织 / 金额，无绕过开关）")
    s.add_argument("fid")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("verify", help="一键自检：定位→写入→回读→联动校验")
    s.add_argument("fid")
    s.add_argument("nos", help="收票单号，多个用逗号分隔")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--with-serial", action="store_true")
    s.add_argument("--allow-steal", action="store_true")
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
