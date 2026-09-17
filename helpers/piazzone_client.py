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

# 凭证来源优先级：环境变量 > piazzone_config.py（本地，不进 git）
# ⚠️ 公开仓库里没有 piazzone_config.py，请复制 piazzone_config.example.py 后填写，
#    或直接设环境变量 PIAZZONE_CLIENT_ID / PIAZZONE_CLIENT_SECRET / PIAZZONE_TAX_NO / PIAZZONE_GHF_MC
try:
    from piazzone_config import (
        TAX_NO, CLIENT_ID, CLIENT_SECRET, ENC_TYPE, API_BASE, GHF_MC,
    )
except ImportError:                                    # 没有本地配置就走环境变量
    TAX_NO = os.environ.get("PIAZZONE_TAX_NO", "")
    CLIENT_ID = os.environ.get("PIAZZONE_CLIENT_ID", "")
    CLIENT_SECRET = os.environ.get("PIAZZONE_CLIENT_SECRET", "")
    GHF_MC = os.environ.get("PIAZZONE_GHF_MC", "")
    API_BASE = os.environ.get("PIAZZONE_BASE_URL", "https://api.piazzone.com")
    ENC_TYPE = int(os.environ.get("PIAZZONE_ENC_TYPE", "0"))

if not CLIENT_ID or CLIENT_ID.startswith("请填"):
    raise SystemExit(
        "❌ 未配置发票云凭证。\n"
        "   方式一（推荐）：复制 helpers/piazzone_config.example.py 为 helpers/piazzone_config.py 并填写；\n"
        "   方式二：设环境变量 PIAZZONE_CLIENT_ID / PIAZZONE_CLIENT_SECRET / PIAZZONE_TAX_NO / PIAZZONE_GHF_MC\n"
        "   注意：金蝶报销主链路（收票信息写入）**不需要**发票云凭证，本模块只是可选兜底。")


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
                time.sleep(1.0)  # 生产 IP TLS 间歇被拦，重试可能命中可用后端
    return {"errcode": "NETWORK", "description": repr(last)}


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
