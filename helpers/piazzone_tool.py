# piazzone_tool.py — 发票云「发票助手PC端 / 综合收票标准版」服务端绑定 · 通用可分发版
#
# 依据官方标准版文档（piaozone-standard.apifox.cn · 发票助手PC web端对接）实现。
# 设计目标：本脚本是「可给其他公司复用的 skill」，不绑定任何一家企业。
#   每家公司接入时，只需提供自己的「发票云企业授权三件套」+ 税号 + 购方名称，
#   即可用智能体完成「发票↔报销单」服务端绑定，员工无需登录金蝶。
#
# 凭证来源（按优先级，均无需改本代码）：
#   1) 环境变量（单公司最快接入，零文件）：
#        PIAZZONE_CLIENT_ID / PIAZZONE_CLIENT_SECRET / PIAZZONE_TAX_NO / PIAZZONE_GHF_MC
#        PIAZZONE_BASE_URL（可选，默认 https://api.piazzone.com）
#   2) 同目录 piazzone_companies.json 中 --company <key> 对应条目（多公司集中管理）
#   3) 内置示例 example-a / example-b / dev（仅演示，可删）
#
# 使用：
#   连通性自检：   python piazzone_tool.py --company example-a
#   绑定发票：     python piazzone_tool.py --company example-a bind <bxd_key> <bill_no> <bill_type> <流水号CSV> [eid] [--bill-type-id FYBX]
#   环境变量接入： PIAZZONE_CLIENT_ID=xxx PIAZZONE_CLIENT_SECRET=yyy PIAZZONE_TAX_NO=zzz python piazzone_tool.py
#
# bill_type（必填类别，对应官方 billType 枚举）：
#   ""   = 费用单据（默认，费用报销单）
#   Tra  = 智能差旅行程单据（差旅费报销单）
#   Pur  = 智能物品采购单据
#   BizOut = 智能对公单据
# --bill-type-id：仅当贵司金蝶金税互联配置需特定金蝶单据类型ID（如 FYBX/CLF）时追加，默认不传。
#
# ─── 域名可达性（2026-09-14 实测，重要）───
# 生产同一台服务器 IP 52.83.114.130 上有两个域名拼写：
#     https://api.piazzone.com   ← 官方环境文档给的，部分企业网关按 SNI 拦截（TLS WRONG_VERSION_NUMBER）
#     https://api.piaozone.com   ← 同一 IP，实测可通，接口行为一致
# 因此本脚本默认用 api.piaozone.com，并内置「域名 failover」：一个不通自动换另一个。
# 无需 hosts 绑定、无需 IT 放行。如两域名都不通，才需网络放行 *.piazzone.com:443 / *.piaozone.com:443。
# 测试环境 https://api-dev.piaozone.com/test 也可直接连。
# 签名：MD5(encType=0)，官方指定（MD5(client_id + client_secret + timestamp)）。

import sys, os, json, time, hashlib, hmac, urllib.request, urllib.error

# ════════════ 内置示例企业（演示用，部署时可删；真实凭证来自配置/环境变量）════════════
BUILTIN = {
    "example-a": {
        "base_url": "https://api.piaozone.com",
        "tax_no": "91110000000000000X",
        "ghf_mc": "示例科技有限公司",
        "client_id": "请填写你的client_id",
        "client_secret": "请填写你的client_secret",
        "enc_type": 0,
    },
    "example-b": {
        "base_url": "https://api.piaozone.com",
        "tax_no": "91510000000000000X",
        "ghf_mc": "示例二科技有限公司",
        "client_id": "请填写你的client_id",
        "client_secret": "请填写你的client_secret",
        "enc_type": 0,
    },
    "dev": {
        "base_url": "https://api-dev.piaozone.com/test",
        "tax_no": "91110000000000000X",
        "ghf_mc": "示例科技有限公司",
        "client_id": "请填测试环境client_id",
        "client_secret": "请填测试环境client_secret",
        "enc_type": 0,
    },
}

# 官方 billType 枚举（发票助手PC端 · getUserKey / 更新单据状态 使用）
BILL_TYPES = {
    "": "费用单据（费用报销单，默认）",
    "Tra": "智能差旅行程单据（差旅费报销单）",
    "Pur": "智能物品采购单据",
    "BizOut": "智能对公单据",
}

# ticketParam：5 位合规校验位，顺序 = [重复报销, 购方抬头, 购方税号, 发票验真, 个票]，1允许/0不允许
TICKET_PARAM_DEFAULT = "00001"


# 域名候选（failover 顺序）：实测 api.piaozone.com 可通、api.piazzone.com 常被网关按 SNI 拦截
DEFAULT_HOSTS = ["https://api.piaozone.com", "https://api.piazzone.com"]


def load_config(company):
    """合并配置：外部 JSON → 内置示例 → 环境变量覆盖。返回该企业最终配置字典。"""
    envs = dict(BUILTIN)
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "piazzone_companies.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                data = json.load(f)
            envs.update(data.get("companies", data))
        except Exception as e:
            print(f"[!] 读取 {cfg_path} 失败：{e}")
    cfg = dict(envs.get(company) or envs.get("example-a"))
    # 环境变量覆盖（单公司零文件接入）
    if os.environ.get("PIAZZONE_CLIENT_ID"):
        cfg["client_id"] = os.environ["PIAZZONE_CLIENT_ID"]
        cfg["client_secret"] = os.environ.get("PIAZZONE_CLIENT_SECRET", cfg.get("client_secret", ""))
        cfg["tax_no"] = os.environ.get("PIAZZONE_TAX_NO", cfg.get("tax_no", ""))
        cfg["ghf_mc"] = os.environ.get("PIAZZONE_GHF_MC", cfg.get("ghf_mc", ""))
    # 域名可独立覆盖（不依赖凭证环境变量）
    if os.environ.get("PIAZZONE_BASE_URL"):
        cfg["base_url"] = os.environ["PIAZZONE_BASE_URL"]
    # ⚠️ 公开仓库里 BUILTIN 只是占位，未填凭证时提前说清楚，别让人以为是接口坏了
    if not cfg.get("client_id") or str(cfg["client_id"]).startswith("请填"):
        raise SystemExit(
            "❌ 未配置发票云凭证（client_id 仍是占位值）。\n"
            "   方式一：复制 helpers/piazzone_config.example.py 为 piazzone_config.py 并填写；\n"
            "   方式二：设环境变量 PIAZZONE_CLIENT_ID / PIAZZONE_CLIENT_SECRET / PIAZZONE_TAX_NO / PIAZZONE_GHF_MC\n"
            "   注意：金蝶报销主链路（收票信息写入）**不需要**发票云凭证，本模块只是可选兜底。")
    cfg.setdefault("base_url", DEFAULT_HOSTS[0])
    # 候选列表：显式域名优先，其余内置别名兜底（网关按 SNI 拦截时自动切换）
    cfg["base_urls"] = [cfg["base_url"]] + [b for b in DEFAULT_HOSTS if b != cfg["base_url"]]
    return cfg


# 全局配置（由 main 按选中企业填充）
API_BASE = TAX_NO = GHF_MC = CLIENT_ID = CLIENT_SECRET = ENC_TYPE = None
BASE_URLS = []          # 域名候选列表（failover 用）

# ════════════ 签名（MD5 encType=0）════════════
def gen_sign(client_id, client_secret, timestamp, enc_type=0):
    payload = f"{client_id}{client_secret}{timestamp}"
    if enc_type == 0:
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
    if enc_type == 1:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if enc_type == 2:
        return hmac.new(client_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raise ValueError(f"不支持 encType={enc_type}")


# ════════════ HTTP（域名 failover + 重试，应对网关按 SNI 拦截）════════════
def _urls():
    """当前域名候选顺序（显式 base_url 优先，其余别名兜底）。"""
    bases = list(BASE_URLS) or []
    if API_BASE and API_BASE not in bases:
        bases = [API_BASE] + bases
    return [b for b in bases if b]


def _promote(base):
    """把实测可通的域名提到最前，后续调用不再空试。"""
    try:
        if base in BASE_URLS and BASE_URLS[0] != base:
            BASE_URLS.remove(base)
            BASE_URLS.insert(0, base)
    except Exception:
        pass


def _request(method, path, body=None, timeout=30, retries=2):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    last = None
    tried = []
    for base in _urls():
        url = f"{base}{path}"
        tried.append(base)
        for _ in range(retries):
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    _promote(base)
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 收到 HTTP 响应 = 网络是通的，业务错误直接返回
                try:
                    out = json.loads(e.read().decode("utf-8"))
                    _promote(base)
                    return out
                except Exception:
                    return {"errcode": str(e.code), "description": str(e.reason)}
            except Exception as e:
                last = e
                break      # 该域名网络不可用 → 换下一个域名
    return {"errcode": "NETWORK", "description": repr(last), "tried": tried}


def _post(path, body, timeout=30, retries=2):
    return _request("POST", path, body, timeout, retries)


def _get(path, timeout=30, retries=2):
    return _request("GET", path, None, timeout, retries)


# ════════════ 发票云接口 ════════════
def get_user_key(bxd_key, ghf_mc=GHF_MC, eid=None, bill_number="", bill_type="",
                 ticket_param=TICKET_PARAM_DEFAULT):
    """获取 userKey：录入单据信息，得到后续页面/服务端调用所需授权。"""
    ts = int(time.time() * 1000)
    eid = eid or _uuid()
    return _post("/m4/fpzs/getUserKey", {
        "timestamp": ts, "client_id": CLIENT_ID, "tin": TAX_NO, "ghf_mc": ghf_mc,
        "eid": eid, "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE), "encType": str(ENC_TYPE),
        "billNumber": bill_number, "bxd_key": bxd_key, "random": _rand(),
        "ticketParam": ticket_param, "billType": bill_type,
    })


def get_link_key():
    return _post("/m4/fpzs/getLinkKey", {})


def cache_entry(bxd_key, bill_no, bill_type, invoice_serial_nos, bill_type_id=None, eid=None, entry_id=""):
    """缓存单据：把发票流水号绑定到报销单（服务端写入 金蝶 RecInvInfo，无需人开浏览器）。
    官方说明：缓存仅 2 小时，须及时 save 才持久。"""
    ts = int(time.time() * 1000)
    eid = eid or _uuid()
    body = {
        "timestamp": ts, "client_id": CLIENT_ID, "eid": eid, "bxd_key": bxd_key,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE), "encType": str(ENC_TYPE),
        "data": [{"entryid": entry_id, "fid": list(invoice_serial_nos)}],
    }
    # 兼容部分金蝶金税互联配置需要单据类型/单号/税号
    if bill_type_id:
        body["billTypeId"] = bill_type_id
    if bill_no:
        body["billnumber"] = bill_no
    if TAX_NO:
        body["tin"] = TAX_NO
    if bill_type:
        body["billType"] = bill_type
    return _post("/m4/fpzs/expense/entry/cache", body)


def save_bill(bxd_key, bill_no, bill_type, invoice_serial_nos, bill_type_id=None, eid=None,
              cost_type_name="", remark="", expense_person_name="", real_amount=""):
    """保存单据：持久化单据与发票的绑定关系。"""
    ts = int(time.time() * 1000)
    eid = eid or _uuid()
    data = [{"entryid": "", "fid": list(invoice_serial_nos), "costTypeId": "", "costTypeName": cost_type_name}]
    body = {
        "userKey": "", "eid": eid, "billnumber": bill_no, "bxd_key": bxd_key,
        "client_id": CLIENT_ID, "tin": TAX_NO, "timestamp": ts,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE), "encType": str(ENC_TYPE),
        "data": data, "remark": remark, "expensePersonId": "", "expensePersonName": expense_person_name,
        "realExpenseAmount": real_amount, "billType": bill_type,
    }
    if bill_type_id:
        body["billTypeId"] = bill_type_id
    return _post("/m4/fpzs/expense/entry/save", body)


def update_status(bxd_key, bill_no, bill_type, expense_status="30", bill_type_id=None, eid=None,
                  invoice_data=None, ticket_param=TICKET_PARAM_DEFAULT):
    """同步单据状态到发票云。expenseStatus: 1未用/30在用/60已用/65已入账。"""
    ts = int(time.time() * 1000)
    eid = eid or _uuid()
    body = {
        "userKey": "", "timestamp": ts, "tin": TAX_NO,
        "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE), "encType": str(ENC_TYPE),
        "client_id": CLIENT_ID, "expenseStatus": expense_status, "ticketParam": ticket_param,
        "billnumber": bill_no, "bxd_key": bxd_key, "eid": eid, "billType": bill_type,
        "invoiceData": invoice_data or [],
    }
    if bill_type_id:
        body["billTypeId"] = bill_type_id
    return _post("/m4/fpzs/expense/invoice/status/update", body)


def delete_bill(bxd_key, bill_no, bill_type, bill_type_id=None, eid=None):
    """删除单据：释放单据下发票（废弃/驳回时调用，使发票回到未用可被其他单据采集）。"""
    ts = int(time.time() * 1000)
    eid = eid or _uuid()
    body = {
        "userKey": "", "timestamp": ts, "tin": TAX_NO, "bxd_key": bxd_key,
        "client_id": CLIENT_ID, "sign": gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE), "encType": str(ENC_TYPE),
        "billnumber": bill_no, "eid": eid, "billType": bill_type,
    }
    if bill_type_id:
        body["billTypeId"] = bill_type_id
    return _post("/m4/fpzs/expense/entry/delete", body)


def query_bill_invoices(bxd_key, ticket_param="11011"):
    ts = int(time.time() * 1000)
    sign = gen_sign(CLIENT_ID, CLIENT_SECRET, ts, ENC_TYPE)
    path = (f"/m4/fpzs/bxdInvoices?bxd_key={bxd_key}&client_id={CLIENT_ID}"
            f"&timestamp={ts}&sign={sign}&ticketParam={ticket_param}&encType={ENC_TYPE}")
    return _get(path)


def bind_invoices_to_expense(bxd_key, bill_no, bill_type, invoice_serial_nos,
                             bill_type_id=None, eid=None, expense_person_name="", remark=""):
    """一步完成：缓存单据(绑定,2h内有效) → 保存单据(持久) → 更新状态(30在用)。返回各步结果。"""
    steps = {}
    steps["cache"] = cache_entry(bxd_key, bill_no, bill_type, invoice_serial_nos,
                                 bill_type_id=bill_type_id, eid=eid)
    if steps["cache"].get("errcode") not in ("0000", None, 0, "0"):
        steps["stopped_at"] = "cache"
        return steps
    steps["save"] = save_bill(bxd_key, bill_no, bill_type, invoice_serial_nos,
                              bill_type_id=bill_type_id, eid=eid,
                              expense_person_name=expense_person_name, remark=remark)
    steps["status"] = update_status(bxd_key, bill_no, bill_type, expense_status="30",
                                    bill_type_id=bill_type_id, eid=eid,
                                    invoice_data=[{"serialNo": s} for s in invoice_serial_nos])
    return steps


# ════════════ 工具 ════════════
def _uuid():
    import uuid
    return str(uuid.uuid4()).replace("-", "")


def _rand():
    import uuid
    return str(uuid.uuid4())[:8]


# ════════════ 命令行入口 ════════════
def main():
    global API_BASE, TAX_NO, GHF_MC, CLIENT_ID, CLIENT_SECRET, ENC_TYPE, BASE_URLS
    args = sys.argv[1:]
    company = "example-a"
    for flag in ("--company", "--org"):
        if flag in args:
            i = args.index(flag)
            company = args[i + 1]
            args = args[:i] + args[i + 2:]
            break
    # 可选 --bill-type-id（金蝶单据类型ID，如 FYBX/CLF）
    bill_type_id = None
    if "--bill-type-id" in args:
        i = args.index("--bill-type-id")
        bill_type_id = args[i + 1]
        args = args[:i] + args[i + 2:]

    cfg = load_config(company)
    API_BASE = cfg["base_url"]; TAX_NO = cfg["tax_no"]; GHF_MC = cfg["ghf_mc"]
    CLIENT_ID = cfg["client_id"]; CLIENT_SECRET = cfg["client_secret"]; ENC_TYPE = cfg.get("enc_type", 0)
    BASE_URLS = list(cfg.get("base_urls") or [API_BASE])

    print(f"当前企业/环境 = {company}  (税号 {TAX_NO})")
    print(f"域名候选（failover） = {BASE_URLS}")
    if bill_type_id:
        print(f"金蝶单据类型ID(billTypeId) = {bill_type_id}")

    if not args or args[0] == "test":
        print("\n>>> 连通性自检：getUserKey（bxd_key=100010, billType=\"\"）...")
        r = get_user_key(bxd_key="100010")
        print(json.dumps(r, ensure_ascii=False, indent=2)[:1000])
        if r.get("errcode") == "NETWORK":
            print("\n[!] 候选域名全部不通（" + "、".join(r.get("tried") or []) + "）。"
                  "本网络对发票云 443 出站被拦：请让 IT 放行 *.piazone.com:443（IP 52.83.114.130），"
                  "或把企业切到 dev 走测试环境。")
        elif r.get("errcode") == "1101":
            print("\n[✓] 网络已通、签名被接受，仅凭证与该企业不匹配 —— 接口/格式/签名全对。")
        elif r.get("errcode") == "1200":
            print("\n[✓] 接口已响应（1200 授权ID不能为空）—— 网络与域名均正常。")
        else:
            print("\n[✓] 返回非 1101，凭证可能被接受，可继续绑定流程。")
        return

    if args[0] == "bind":
        if len(args) < 5:
            print("用法：python piazzone_tool.py --company <key> bind <bxd_key> <bill_no> <bill_type> <流水号CSV> [eid] [--bill-type-id FYBX]")
            print("  bill_type 取值：''=费用报销单  Tra=差旅费报销单  Pur=采购  BizOut=对公")
            print("例：  python piazzone_tool.py --company example-a bind 100010 FYBX001 \"\" 118390001,118390002")
            print("例：  python piazzone_tool.py --company example-a bind 100010 FYBX001 Tra 118390001 --bill-type-id FYBX")
            return
        bxd_key = args[1]; bill_no = args[2]; bill_type = args[3]
        serials = [s.strip() for s in args[4].split(",") if s.strip()]
        eid = args[5] if len(args) > 5 else None
        if bill_type not in BILL_TYPES:
            print(f"[!] bill_type 非法：{bill_type}。合法值：{list(BILL_TYPES.keys())}（含义见文件头）。")
            return
        print(f"\n>>> 绑定发票 {serials} 到报销单 {bxd_key}({bill_no}) billType={bill_type} ...")
        res = bind_invoices_to_expense(bxd_key, bill_no, bill_type, serials,
                                       bill_type_id=bill_type_id, eid=eid)
        print(json.dumps(res, ensure_ascii=False, indent=2)[:2000])
        if "status" in res:
            print("\n>>> 验证绑定结果：")
            print(json.dumps(query_bill_invoices(bxd_key), ensure_ascii=False, indent=2)[:1000])
        return

    print("未知命令。支持：无参(自检) / bind ...")


if __name__ == "__main__":
    main()
