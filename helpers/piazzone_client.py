"""
piazzone_client.py — 发票云(标准版)「发票助手 PC 端对接」服务端客户端

这是金蝶云星空「综合收票管理(标准版)」对接发票云的标准途径：
「费用报销单 → 选择发票」按钮（发票无忧助手）的服务端等价物。
通过本模块，agent 可绕过坏掉的客户端按钮，在服务端完成「发票 ↔ 报销单」绑定，
从而实现：员工不登录金蝶、只需在对话框发发票图 → agent 后台完成全部动作。

⚠️ 关键结论（来自官方对接文档「发票助手PC web端对接文档.html」）：
- 绑定发票到报销单，**不需要人开浏览器**。服务端接口 `executeEntryCache`（缓存单据）
  直接传发票流水号(fid) 即可写入金蝶 RecInvInfo。这正是「员工不登录」的技术可行路径。
- 签名必须用 **MD5(encType=0)**： sign = MD5(client_id + client_secret + timestamp)

完整服务端链路（无人交互模式）：
  1. get_user_key    录入单据信息，获取 userKey（按 bxd_key=报销单ID）
  2. get_link_key    获取 linkKey（建立 socket 通道用；纯服务端缓存模式可省略）
  3. cache_entry     缓存单据：把发票流水号绑定到报销单（核心写入动作，无需人）
  4. save_bill       保存单据：持久化绑定关系
  5. update_status   更新发票状态：同步 expenseStatus（1未用/30在用/60已用/65已入账）

（另有 token + polling/socket 模式，是给「人开浏览器选票」实时回推用的，自动化不需要。）

凭证来自 piazzone_config.py（本地，不进 git）
"""

import time
import json
import uuid
import hashlib
import hmac
import os
import urllib.request
import urllib.error
import ssl

# 凭证来源优先级：环境变量 > **系统加密凭据（DPAPI）** > piazzone_config.py（本地，不进 git）
# ⚠️ 公开仓库里没有 piazzone_config.py。推荐用配置向导填写：
#      python helpers/setup_credentials.py   → 「可选：发票云」一组字段
#    凭据经 Windows 系统加密保存，明文不落盘、不进对话记录。
_CFG = {"TAX_NO": "", "CLIENT_ID": "", "CLIENT_SECRET": "", "ENCRYPT_KEY": "",
        "GHF_MC": "", "API_BASE": "https://api.piaozone.com", "ENC_TYPE": 0}

# ① 本地明文配置（最低优先级，兜底）。用 getattr 逐个取，缺某个常量也不至于整块失效。
try:
    import piazzone_config as _pc  # type: ignore
    _CFG["TAX_NO"] = getattr(_pc, "TAX_NO", "") or ""
    _CFG["CLIENT_ID"] = getattr(_pc, "CLIENT_ID", "") or ""
    _CFG["CLIENT_SECRET"] = getattr(_pc, "CLIENT_SECRET", "") or ""
    _CFG["ENCRYPT_KEY"] = getattr(_pc, "ENCRYPT_KEY", "") or ""
    _CFG["GHF_MC"] = getattr(_pc, "GHF_MC", "") or ""
    _CFG["API_BASE"] = getattr(_pc, "API_BASE", "") or _CFG["API_BASE"]
    _CFG["ENC_TYPE"] = int(getattr(_pc, "ENC_TYPE", 0) or 0)
except Exception:  # noqa: BLE001  （没有本地配置很正常）
    pass

# ② 系统加密凭据（推荐）。**支持多组织**。
# ⚠️ 向导/加密存储里用的是**小写键**（client_id / client_secret / …），
#    本模块内部用大写，必须显式映射 —— 直接按同名取会永远取不到（实测踩过）。
PIAZZONE_ORG_USED = ""          # 本次实际选用的组织，便于排障（只读）


def _split_orgs(raw) -> list:
    """「对应金蝶组织」字段 → 编码列表。逗号 / 顿号 / 空格分隔都认。"""
    return [c.strip() for c in
            str(raw or "").replace("，", ",").replace("、", ",").replace(" ", ",").split(",")
            if c.strip()]


def _load_raw_piazzone():
    """读加密存储里的 `piazzone` 段（读不到返回 None）。"""
    try:
        import credstore as _cs  # 同目录，纯标准库
        return (_cs.load() or {}).get("piazzone") if _cs.available() else None
    except Exception:  # noqa: BLE001
        return None


def _normalize_orgs(pz) -> dict:
    """归一成 `{组织名: 条目}`；兼容旧的**扁平单组织**结构。"""
    if not isinstance(pz, dict) or not pz:
        return {}
    if not isinstance(pz.get("orgs"), dict):                 # 旧扁平结构
        return {"(默认)": pz} if pz.get("client_id") else {}
    return {k: v for k, v in pz["orgs"].items()
            if isinstance(v, dict) and v.get("client_id")}


def _pick_piazzone_org(pz: dict, kingdee_org: str = "", want_org: str = "",
                       org_name: str = "") -> tuple[dict, str, str]:
    """从加密存储的 `piazzone` 段里挑出本次要用的组织。

    选择顺序：`want_org` / `PIAZZONE_ORG`（按组织名 / 税号 / 企业全称匹配）
              > `kingdee_org` / `KINGDEE_ORG`（按登记的「金蝶组织编码」匹配）
              > `org_name`（**按金蝶组织名称 ↔ 购方企业全称自动匹配**，界面不需要人工维护）
              > 存储里的 `active` > 第一个组织。

    返回 `(条目, 组织名, 命中方式)`；命中方式 ∈ name / kingdee / orgname / active / first。
    兼容旧的**扁平单组织**结构。
    """
    orgs = _normalize_orgs(pz)
    if not orgs:
        return {}, "", ""
    want = (want_org or os.environ.get("PIAZZONE_ORG") or "").strip()
    if want:
        for k, v in orgs.items():
            if want in (k, str(v.get("ghf_mc") or ""), str(v.get("tax_no") or "")):
                return v, k, "name"
    kd = (kingdee_org or os.environ.get("KINGDEE_ORG") or "").strip()
    if kd:
        for k, v in orgs.items():
            if kd in _split_orgs(v.get("kingdee_orgs")):
                return v, k, "kingdee"
    nm = str(org_name or "").strip()
    if len(nm) >= 4:                       # 太短的名字不做模糊匹配，免得误命中
        for k, v in orgs.items():
            if nm in (k, str(v.get("ghf_mc") or "")):
                return v, k, "orgname"
        for k, v in orgs.items():          # 宽松一轮：金蝶组织名可能是简称/带后缀
            g = str(v.get("ghf_mc") or "").strip() or k
            if g and (nm in g or g in nm):
                return v, k, "orgname"
    act = str(pz.get("active") or "")
    if act in orgs:
        return orgs[act], act, "active"
    k = next(iter(orgs))
    return orgs[k], k, "first"


def list_piazzone_orgs() -> list:
    """列出加密存储里已配置的组织名（供排障 / 智能体提示用）。"""
    return list(_normalize_orgs(_load_raw_piazzone()).keys())


def describe_piazzone_orgs() -> list:
    """`[(组织名, 购方企业全称, 购方税号, 对应金蝶组织)]` —— 用于确认「到底配了哪几家」。"""
    return [(k, str(v.get("ghf_mc") or ""), str(v.get("tax_no") or ""),
             str(v.get("kingdee_orgs") or ""))
            for k, v in _normalize_orgs(_load_raw_piazzone()).items()]


# ③ 环境变量（最高优先级，适合 CI / 无人值守）
_ENV_OVERRIDES: dict = {}
for _env, _key, _cast in (("PIAZZONE_TAX_NO", "TAX_NO", str),
                          ("PIAZZONE_CLIENT_ID", "CLIENT_ID", str),
                          ("PIAZZONE_CLIENT_SECRET", "CLIENT_SECRET", str),
                          ("PIAZZONE_GHF_MC", "GHF_MC", str),
                          ("PIAZZONE_BASE_URL", "API_BASE", str),
                          ("PIAZZONE_ENC_TYPE", "ENC_TYPE", int)):
    _v = os.environ.get(_env)
    if _v not in (None, ""):
        _ENV_OVERRIDES[_key] = _cast(_v)

_BASE_CFG = dict(_CFG)          # 基准值 = 本地明文配置（最低优先级）


def _apply_org(org: dict) -> None:
    """把组织条目的值叠加到基准值、再叠加环境变量，写入模块级常量。

    ⚠️ 环境变量永远最高优先 —— 显式设了 `PIAZZONE_CLIENT_ID` 之类的，
    即使随后调 `select_org()` 切组织也不会被覆盖。
    """
    global TAX_NO, CLIENT_ID, CLIENT_SECRET, ENCRYPT_KEY, GHF_MC, API_BASE, ENC_TYPE
    eff = dict(_BASE_CFG)
    for _k, _pk in (("CLIENT_ID", "client_id"), ("CLIENT_SECRET", "client_secret"),
                    ("ENCRYPT_KEY", "encrypt_key"), ("TAX_NO", "tax_no"),
                    ("GHF_MC", "ghf_mc"), ("API_BASE", "base_url")):
        _val = (org or {}).get(_pk)
        if _val not in (None, ""):
            eff[_k] = _val
    if (org or {}).get("enc_type") not in (None, ""):
        eff["ENC_TYPE"] = int(org["enc_type"])
    eff.update(_ENV_OVERRIDES)
    TAX_NO = eff["TAX_NO"]
    CLIENT_ID = eff["CLIENT_ID"]
    CLIENT_SECRET = eff["CLIENT_SECRET"]
    ENCRYPT_KEY = eff["ENCRYPT_KEY"]
    GHF_MC = eff["GHF_MC"]
    API_BASE = eff["API_BASE"]
    ENC_TYPE = eff["ENC_TYPE"]


def select_org(kingdee_org: str = "", org: str = "", org_name: str = "") -> dict:
    """**按金蝶组织切换本次使用的发票云企业授权**（多收票组织的关键）。

    发票云的权益是**按税号维度**划分的：每个购买了收票服务的税号，各有一组
    `client_id / client_secret / 购方税号`（官方原文：「请使用税号对应的企业授权，
    不要用租户授权」）。所以同一账套下 101 / 104 两家都开了收票时，必须按单据的
    **金蝶组织**选用对应的那一组 —— 用错组织会带上错误的购方税号，上传与建票都白做。

    三种指定方式（任选其一即可，越靠前优先级越高）::

        piazzone_client.select_org(kingdee_org="104")                # 金蝶组织编码
        piazzone_client.select_org(org_name="示例三科技")             # 金蝶组织名称（**推荐**）
        piazzone_client.select_org(org="91110000XXXXXXXXXX")         # 组织名/税号/企业全称

    `org_name` 是与**购方企业全称**比对 —— 界面上不需要再单独维护「组织名」或
    「对应金蝶组织」，配了几家就按公司名自动对上（`recvin_link.upload_invoice`
    会自动从报销单里读出组织名称再调这里）。

    返回 `{"name": 选中的组织名, "matched": 命中方式, "candidates": [全部组织名]}`。
    命中方式 ∈ name / kingdee / orgname / active / first。
    ⚠️ `candidates` 多于一个、而 `matched` 落到 **active/first**，说明该组织
    没对上任何一家 —— 调用方应停下来提示用户核对，**不要**拿另一家的授权硬跑。
    """
    global PIAZZONE_ORG_USED
    pz = _load_raw_piazzone()
    picked, name, how = _pick_piazzone_org(pz, kingdee_org=kingdee_org, want_org=org,
                                           org_name=org_name)
    if picked:
        _apply_org(picked)
        PIAZZONE_ORG_USED = name
    return {"name": PIAZZONE_ORG_USED, "matched": how,
            "candidates": list(_normalize_orgs(pz).keys())}


_picked, PIAZZONE_ORG_USED, _PICK_HOW = _pick_piazzone_org(_load_raw_piazzone())
_apply_org(_picked)

if not CLIENT_ID or CLIENT_ID.startswith("请填"):
    raise SystemExit(
        "❌ 未配置发票云凭证（本模块是**可选**功能）。\n"
        "   方式一（推荐）：运行配置向导，填「可选：发票云」那一组\n"
        "       python helpers/setup_credentials.py\n"
        "       （只补发票云、不动金蝶凭据：python helpers/setup_credentials.py --piazzone-child）\n"
        "       （凭据经 Windows 系统加密保存，明文不落盘、不进对话）\n"
        "   方式二：设环境变量 PIAZZONE_CLIENT_ID / PIAZZONE_CLIENT_SECRET / PIAZZONE_TAX_NO / PIAZZONE_GHF_MC\n"
        "   方式三：复制 helpers/piazzone_config.example.py 为 helpers/piazzone_config.py 并填写（明文，不推荐）\n"
        "   ⚠️ 凭据请向「发票云（票总管）」客服索取，或登录 open-ultimate.piaozone.com 查看。\n"
        "   注意：金蝶报销主链路（收票信息写入 / 明细对齐 / 附件 / 提交）**不需要**发票云凭证。")


# ───────────────────────── 签名（官方：MD5 encType=0） ─────────────────────────
def gen_sign(client_id: str, client_secret: str, timestamp: int, enc_type: int = ENC_TYPE) -> str:
    payload = f"{client_id}{client_secret}{timestamp}"
    if enc_type == 0:
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
    if enc_type == 1:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if enc_type == 2:
        return hmac.new(client_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raise ValueError(f"不支持的 encType={enc_type}")


def _post(path: str, body: dict, *, timeout: int = 30, retries: int = 3) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"errcode": str(e.code), "description": e.reason}
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.0)
    # ⚠️ 排错提示：这个模块曾经因为 base_url 拼写错误（api.piazzone.com，多一个 z）
    #    被误判成"TLS 墙 / 需要 IT 放行"，白查了两个月。若这里报 SSL/EOF 类错误，
    #    第一件事就是核对域名拼写：正确是 **api.piaozone.com**（piaozone，一个 z）。
    msg = repr(last)
    if any(k in msg for k in ("SSL", "EOF", "WRONG_VERSION", "CERTIFICATE", "Connection reset")):
        msg += ("\n      ↳ 若为 SSL/TLS 类失败，先核对 API_BASE 域名拼写："
                "正确为 https://api.piaozone.com（piaozone 一个 z）。"
                "写成 piazzone（两个 z）会 TLS 失败，且该域名根本不存在，"
                "手动绑 hosts 也无效（SNI 对不上）。")
    return {"errcode": "NETWORK", "description": msg}


def _get(path: str, *, timeout: int = 30, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        req = urllib.request.Request(f"{API_BASE}{path}", headers={"Content-Type": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.0)
    return {"errcode": "NETWORK", "description": repr(last)}


# ───────────────────────── 0. 获取 access_token（socket/polling 模式用） ─────────────────────────
def get_token() -> dict:
    """base/oauth/token —— 仅 socket/polling 实时选票模式需要；纯服务端缓存模式可不用。"""
    ts = int(time.time() * 1000)
    body = {
        "client_id": CLIENT_ID,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, 0),
        "timestamp": ts,
    }
    return _post("/base/oauth/token", body)


# ───────────────────────── 1. getUserKey ─────────────────────────
def get_user_key(bxd_key: str, ghf_mc: str = GHF_MC, eid: str | None = None,
                 bill_number: str = "", ticket_param: str = "1101") -> dict:
    """获取 userKey（录入单据信息，获取后续调用的授权）。

    官方参数：timestamp, client_id, tin, ghf_mc, eid, sign, encType,
              billNumber, bxd_key, random, ticketParam
    bxd_key : 报销单 ID（金蝶侧全球唯一ID；草稿单无编号时可传 FID，或传 billNumber 一致值）
    ghf_mc  : 购货方名称（校验发票抬头一致性）
    eid     : 接入企业用户 ID（金蝶/EAS 用户ID；非随便 uuid，应传真实 EAS 用户ID）
    """
    ts = int(time.time() * 1000)
    eid = eid or str(uuid.uuid4()).replace("-", "")
    body = {
        "timestamp": ts,
        "client_id": CLIENT_ID,
        "tin": TAX_NO,
        "ghf_mc": ghf_mc,
        "eid": eid,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE),
        "encType": ENC_TYPE,
        "billNumber": bill_number,
        "bxd_key": bxd_key,
        "random": str(uuid.uuid4())[:8],
        "ticketParam": ticket_param,
    }
    return _post("/m4/fpzs/getUserKey", body)


# ───────────────────────── 2. getLinkKey ─────────────────────────
def get_link_key() -> dict:
    """/m4/fpzs/getLinkKey —— 官方写明「请求参数：无」，返回 data=linkKey。
    纯服务端缓存绑定模式可省略。"""
    return _post("/m4/fpzs/getLinkKey", {})


# ───────────────────────── 3. 缓存单据（核心：绑定发票↔报销单） ─────────────────────────
def cache_entry(bxd_key: str, bill_no: str, bill_type_id: str,
                invoice_serial_nos: list, eid: str | None = None,
                entry_id: str = "") -> dict:
    """executeEntryCache —— 把发票流水号绑定到报销单（写入金蝶 RecInvInfo 的服务端动作）。

    官方参数：timestamp, client_id, eid, bxd_key, billTypeId, billnumber, tin, data, sign
    data 格式：[{"entryid": "", "fid": ["流水号1", "流水号2"]}]   # entryid 无分录可传空
    invoice_serial_nos：发票流水号列表（来自发票云池，非金蝶收票单 FID）
    bill_type_id：发票云侧单据类型编码（FYBX/CLF 在发票云侧的映射，需实施方确认）
    """
    ts = int(time.time() * 1000)
    eid = eid or str(uuid.uuid4()).replace("-", "")
    data = [{"entryid": entry_id, "fid": list(invoice_serial_nos)}]
    body = {
        "timestamp": ts,
        "client_id": CLIENT_ID,
        "eid": eid,
        "bxd_key": bxd_key,
        "billTypeId": bill_type_id,
        "billnumber": bill_no,
        "tin": TAX_NO,
        "data": data,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE),
        "encType": ENC_TYPE,
    }
    return _post("/m4/fpzs/expense/entry/cache", body)


# ───────────────────────── 4. 保存单据（持久化绑定） ─────────────────────────
def save_bill(bxd_key: str, bill_no: str, bill_type_id: str,
              invoice_serial_nos: list, eid: str | None = None,
              cost_type_name: str = "", remark: str = "",
              expense_person_name: str = "", real_amount: str = "") -> dict:
    """/m4/fpzs/expense/entry/save —— 持久化发票与报销单的绑定关系。

    官方参数（节选）：userKey(可空), eid, billTypeId, billnumber, bxd_key,
    client_id, tin, timestamp, sign, encType, data[{entryid, fid[], costTypeId, costTypeName}],
    remark, expensePersonId, expensePersonName, realExpenseAmount, billType
    """
    ts = int(time.time() * 1000)
    eid = eid or str(uuid.uuid4()).replace("-", "")
    data = [{
        "entryid": "",
        "fid": list(invoice_serial_nos),
        "costTypeId": "",
        "costTypeName": cost_type_name,
    }]
    body = {
        "userKey": "",
        "eid": eid,
        "billTypeId": bill_type_id,
        "billnumber": bill_no,
        "bxd_key": bxd_key,
        "client_id": CLIENT_ID,
        "tin": TAX_NO,
        "timestamp": ts,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE),
        "encType": ENC_TYPE,
        "data": data,
        "remark": remark,
        "expensePersonId": "",
        "expensePersonName": expense_person_name,
        "realExpenseAmount": real_amount,
        "billType": bill_type_id,
    }
    return _post("/m4/fpzs/expense/entry/save", body)


# ───────────────────────── 5. 更新发票状态 ─────────────────────────
def update_status(bxd_key: str, bill_no: str, bill_type_id: str,
                  expense_status: str = "30", eid: str | None = None,
                  invoice_data: list | None = None, ticket_param: str = "1101") -> dict:
    """/m4/fpzs/expense/invoice/status/update —— 同步单据状态到发票云。

    expense_status: 1未用、30在用、60已用、65已入账
    invoice_data: [{serialNo, canBeDeduction, entryAmount, outputReason, outputAmount}]
    ⚠️ 逆向流程：单据废弃/删除/驳回时须把状态调回 1，否则发票被永久占用。
    """
    ts = int(time.time() * 1000)
    eid = eid or str(uuid.uuid4()).replace("-", "")
    body = {
        "userKey": "",
        "timestamp": ts,
        "tin": TAX_NO,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE),
        "encType": ENC_TYPE,
        "client_id": CLIENT_ID,
        "expenseStatus": expense_status,
        "ticketParam": ticket_param,
        "billnumber": bill_no,
        "bxd_key": bxd_key,
        "eid": eid,
        "billTypeId": bill_type_id,
        "invoiceData": invoice_data or [],
    }
    return _post("/m4/fpzs/expense/invoice/status/update", body)


# ───────────────────────── 6. 查询单据下发票（验证绑定结果） ─────────────────────────
def query_bill_invoices(bxd_key: str, ticket_param: str = "11011") -> dict:
    """/m4/fpzs/bxdInvoices?bxd_key=... —— 通过报销单 ID 查询已关联发票，验证绑定是否生效。"""
    ts = int(time.time() * 1000)
    sign = gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE)
    path = (f"/m4/fpzs/bxdInvoices?bxd_key={bxd_key}&client_id={CLIENT_ID}"
            f"&timestamp={ts}&sign={sign}&ticketParam={ticket_param}&encType={ENC_TYPE}")
    return _get(path)


# ───────────────────────── 编排：服务端自动绑定（无需人开浏览器） ─────────────────────────
def bind_invoices_to_expense(bxd_key: str, bill_no: str, bill_type_id: str,
                             invoice_serial_nos: list, eid: str | None = None,
                             expense_person_name: str = "", remark: str = "") -> dict:
    """一步完成：缓存单据(绑定) → 保存单据 → 更新状态(30在用)。
    返回各步结果，便于排查。这是「员工不登录金蝶」的核心动作。"""
    steps = {}
    steps["cache"] = cache_entry(bxd_key, bill_no, bill_type_id, invoice_serial_nos, eid=eid)
    if steps["cache"].get("errcode") not in ("0000", None, 0, "0"):
        steps["stopped_at"] = "cache"
        return steps
    steps["save"] = save_bill(bxd_key, bill_no, bill_type_id, invoice_serial_nos,
                               eid=eid, expense_person_name=expense_person_name, remark=remark)
    steps["status"] = update_status(bxd_key, bill_no, bill_type_id,
                                    expense_status="30", eid=eid,
                                    invoice_data=[{"serialNo": s} for s in invoice_serial_nos])
    return steps


if __name__ == "__main__":
    print("签名自检 encType=%d:" % ENC_TYPE, gen_sign(CLIENT_ID, CLIENT_SECRET, 1234567890000))
    print("当前 API_BASE =", API_BASE)
    print("\n>>> 测试 getUserKey (bxd_key=100010)...")
    try:
        r = get_user_key(bxd_key="100010", ghf_mc=GHF_MC)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:800])
    except Exception as e:
        print("getUserKey 异常:", repr(e))
