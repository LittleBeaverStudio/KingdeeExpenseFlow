# -*- coding: utf-8 -*-
"""金蝶凭据配置向导（弹窗）—— **明文不落盘**。

> 本文件是 **kingdee-expense-flow（报销助手）专版**，比导出/分析技能的版本多
> 「发票云」一组（且支持**多组织**）。导出 / 分析技能不需要发票云，它们的同名脚本不含该组。

三条硬规矩
──────────
1. **不经过对话**：凭据只在本地弹窗里输入，不进任何聊天记录。
2. **不落明文**：敏感字段经 DPAPI 加密写入 `~/.workbuddy/kingdee/credentials.bin`。
3. **不回显、不写日志**：输入框掩码；只打印字段名是否存在，永不打印值。

用法
────
  python helpers/setup_credentials.py              # 配置窗口
  python helpers/setup_credentials.py --change-key # 「修改密钥」模式
  python helpers/setup_credentials.py --check      # 只看状态，不开窗
  python helpers/setup_credentials.py --delete     # 清除已保存凭据

> **用户不需要打开这个 .py，也不需要敲命令** —— 由智能体代为调用，窗口弹在用户屏幕上。

⚠️ 实现注意（踩过的坑）
──────────────────────
`ttk.Entry` **不会**持有 `StringVar` 的 Python 引用。若在循环里 `var = tk.StringVar()`
逐轮重新赋值，上一轮的 StringVar 会被 GC，而 `tkinter.Variable.__del__` 会 unset 掉
Tcl 变量 → **输入框被清空、预填全部丢失**（实测四个框全空）。
所以本文件用 `VAR` 字典**持有所有 StringVar 的引用**，窗口不关就不释放。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import credstore  # noqa: E402

LEGACY_CONFIG = Path.home() / ".workbuddy" / "kingdee" / "config.json"
PIAZZONE_DEFAULT_BASE = "https://api.piaozone.com"

_EXPORTER_CANDIDATES = (
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent.parent,
    Path.home() / ".workbuddy" / "skills" / "kingdee-data-exporter",
    Path.home() / ".workbuddy" / "skills" / "KingdeeDataExporter",
)


# ─────────────────────────── 预填来源 ───────────────────────────

def _read_legacy_config() -> dict:
    if not LEGACY_CONFIG.is_file():
        return {}
    try:
        data = json.loads(LEGACY_CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    profiles = data.get("profiles")
    if isinstance(profiles, dict) and profiles:
        want = os.environ.get("KINGDEE_PROFILE") or str(data.get("default") or "")
        if not want:
            want = next(iter(profiles))
        picked = profiles.get(want)
        if isinstance(picked, dict):
            common = {k: v for k, v in data.items() if k not in ("profiles", "default")}
            merged = {**common, **picked}
            for old, new in (("server", "base_url"), ("acct", "acctid"),
                             ("user", "username"), ("pwd", "password")):
                if old in merged and new not in merged:
                    merged[new] = merged.pop(old)
            return merged
    return data


def _read_exporter_config() -> dict:
    for cand in _EXPORTER_CANDIDATES:
        if not (Path(cand) / "config.py").is_file():
            continue
        try:
            sys.path.insert(0, str(cand))
            from config import KINGDEE_CONFIG as KC  # type: ignore
            if isinstance(KC, dict):
                return {k: v for k, v in KC.items() if v}
        except Exception:  # noqa: BLE001
            continue
        finally:
            if str(cand) in sys.path:
                sys.path.remove(str(cand))
    return {}


def collect_prefill() -> dict:
    pre: dict = {}
    pre.update({k: v for k, v in _read_exporter_config().items()})
    pre.update({k: v for k, v in _read_legacy_config().items() if v})
    saved = credstore.load()
    if saved:
        pre.update({k: v for k, v in saved.items() if v})
    return pre


def _lookup_acct_name(base_url: str, acctid: str, timeout: int = 8) -> str:
    """免认证接口按 acctid 查账套名（**纯标准库**）。

    刻意不 import 导出技能 —— 那会触发强制迁移闸门。失败一律返回空串，绝不阻塞窗口。
    """
    import urllib.request
    import base64 as _b64
    import gzip as _gzip

    base = str(base_url or "").strip().rstrip("/")
    acctid = str(acctid or "").strip()
    if not base or not acctid:
        return ""
    if not base.lower().endswith("/k3cloud"):
        base += "/k3cloud"
    url = (base + "/Kingdee.BOS.ServiceFacade.ServicesStub.Account.AccountService."
           "GetDataCenterList.common.kdsvc")
    try:
        req = urllib.request.Request(url, data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace").strip()
        try:
            data = json.loads(text)
        except ValueError:
            if text.startswith("H4sI"):
                data = json.loads(_gzip.decompress(_b64.b64decode(text)).decode("utf-8", "replace"))
            else:
                return ""
        if isinstance(data, str):
            data = json.loads(data)
        rows = data if isinstance(data, list) else next(
            (data[k] for k in ("Data", "data", "DataCenterList", "Result")
             if isinstance(data, dict) and isinstance(data.get(k), list)), [])
        for item in rows:
            if not isinstance(item, dict):
                continue
            low = {str(k).lower(): v for k, v in item.items()}
            if str(low.get("id") or "").strip() == acctid:
                return str(low.get("name") or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ─────────────────── 发票云：多组织数据模型（兼容旧扁平结构） ───────────────────

_PZ_FIELDS = ("ghf_mc", "tax_no", "client_id", "client_secret", "kingdee_orgs")


def _piazzone_state(pz) -> tuple[dict, str]:
    """把存储里的 piazzone 归一成 `(orgs, active)`，并把旧的扁平单组织升级上来。"""
    if not isinstance(pz, dict) or not pz:
        return {}, ""
    if isinstance(pz.get("orgs"), dict):
        orgs = {str(k): dict(v) for k, v in pz["orgs"].items() if isinstance(v, dict)}
        active = str(pz.get("active") or "")
        if not active and orgs:
            active = next(iter(orgs))
        return orgs, active
    if pz.get("client_id"):  # 旧结构：单组织平铺
        key = str(pz.get("ghf_mc") or pz.get("tax_no") or "默认组织")
        return {key: {k: pz.get(k, "") for k in _PZ_FIELDS}}, key
    return {}, ""


def _piazzone_payload(orgs: dict, active: str) -> dict:
    clean = {}
    for k, v in (orgs or {}).items():
        org = {f: (v.get(f) or "") for f in _PZ_FIELDS}
        if not org["client_id"]:
            continue
        org["base_url"] = str(v.get("base_url") or PIAZZONE_DEFAULT_BASE)
        org["enc_type"] = int(v.get("enc_type") or 0)
        clean[str(k)] = org
    if not clean:
        return {}
    if active not in clean:
        active = next(iter(clean))
    return {"active": active, "orgs": clean}


def _orgs_from_rows(rows: list) -> tuple:
    """界面上的各行 → `(orgs, active)`。

    **组织名 = 购方企业全称**（自动取，不再单独问用户）；`kingdee_orgs` 不再人工维护
    （路由改为按「单据组织名称 ↔ 购方企业全称」自动匹配）。
    没填 `client_id` 的整行忽略 —— 免得存下没用的空壳。
    """
    out: dict = {}
    for row in rows or []:
        org = {f: str((row or {}).get(f) or "").strip() for f in _PZ_FIELDS}
        if not org["client_id"]:
            continue
        name = org["ghf_mc"] or org["tax_no"] or ("公司%d" % (len(out) + 1))
        org["kingdee_orgs"] = ""
        org["base_url"] = PIAZZONE_DEFAULT_BASE
        org["enc_type"] = 0
        out[name] = org
    return out, next(iter(out), "")


# ─────────────────────────────── 界面 ───────────────────────────────

def run_gui(change_key: bool = False) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    pre = collect_prefill()
    legacy_has_secret = bool(str(_read_legacy_config().get("password") or "").strip())
    pz_orgs, _pz_active = _piazzone_state(pre.get("piazzone"))

    # 账套名：能取就补上（免认证接口，失败就留空，不影响保存）
    acct_name = str(pre.get("acct_name") or "").strip()
    if not acct_name and pre.get("acctid"):
        acct_name = _lookup_acct_name(pre.get("base_url"), pre.get("acctid"))

    root = tk.Tk()
    root.title("金蝶云星空 · 修改密钥" if change_key else "金蝶云星空 · 凭据配置")
    root.resizable(False, True)
    root.attributes("-topmost", True)     # 配置窗不被微信之类压住（配置完即关，不碍事）

    # ── 观感：不用系统默认（那个看着像 Win98），改 clam + 扁平化配色 ──
    PAGE_BG = "#f2f4f7"      # 窗体底
    CARD_BG = "#ffffff"      # 内容卡片
    TEXT_C = "#1f2733"
    MUTED_C = "#6b7684"
    BORDER_C = "#dfe3e8"
    ACCENT_C = "#2f6fed"
    FS = "Microsoft YaHei UI"
    root.configure(bg=PAGE_BG)
    st = ttk.Style()
    try:
        st.theme_use("clam")                 # 只有 clam 允许随意改字段背景/边框色
    except Exception:  # noqa: BLE001
        pass
    st.configure(".", font=(FS, 10), background=CARD_BG, foreground=TEXT_C)
    st.configure("TLabel", background=CARD_BG, foreground=TEXT_C)
    st.configure("H.TLabel", background=CARD_BG, foreground=TEXT_C, font=(FS, 11, "bold"))
    st.configure("Muted.TLabel", background=CARD_BG, foreground=MUTED_C, font=(FS, 9))
    st.configure("Card.TFrame", background=CARD_BG)
    st.configure("Page.TFrame", background=PAGE_BG)
    st.configure("Page.TLabel", background=PAGE_BG, foreground=TEXT_C)
    st.configure("PZBorder.TFrame", background="#e6eaf0")     # 公司卡片的 1px 边框
    st.configure("PZCard.TFrame", background="#f8fafc")       # 公司卡片底（比白略灰，成组感）
    st.configure("PZ.TLabel", background="#f8fafc", foreground=TEXT_C)
    st.configure("Mini.TButton", padding=(0, 0), relief="flat",
                 bordercolor=BORDER_C, lightcolor="#ffffff", darkcolor="#ffffff",
                 background="#ffffff", foreground=MUTED_C, font=(FS, 11))
    st.map("Mini.TButton", background=[("active", "#eef2f7"), ("pressed", "#e4e9f0")],
           foreground=[("disabled", "#c8d0da")], bordercolor=[("active", "#c8d0da")])
    st.configure("TEntry", fieldbackground="#ffffff", background="#ffffff",
                 bordercolor=BORDER_C, lightcolor=BORDER_C, darkcolor=BORDER_C,
                 foreground=TEXT_C, insertcolor=TEXT_C, padding=(9, 6))
    st.map("TEntry", bordercolor=[("focus", ACCENT_C)],
           lightcolor=[("focus", ACCENT_C)], darkcolor=[("focus", ACCENT_C)])
    st.configure("TButton", background="#ffffff", foreground=TEXT_C,
                 bordercolor=BORDER_C, lightcolor="#ffffff", darkcolor="#ffffff",
                 focuscolor=CARD_BG, padding=(14, 6), relief="flat")
    st.map("TButton", background=[("active", "#eef2f7"), ("pressed", "#e4e9f0")],
           bordercolor=[("active", "#c8d0da")])
    st.configure("Primary.TButton", background=ACCENT_C, foreground="#ffffff",
                 bordercolor=ACCENT_C, lightcolor=ACCENT_C, darkcolor=ACCENT_C,
                 focuscolor=ACCENT_C, padding=(20, 7), relief="flat")
    st.map("Primary.TButton", background=[("active", "#1f5fd8"), ("pressed", "#1a52bd")],
           bordercolor=[("active", "#1f5fd8")], foreground=[("disabled", "#e9eef7")])

    # ★ 持有所有 StringVar 的引用 —— 否则 ttk.Entry 的预填会被 GC 清空
    VAR: dict[str, tk.StringVar] = {}

    def sv(key: str, value: str = "") -> tk.StringVar:
        VAR[key] = tk.StringVar(value=value)
        return VAR[key]

    # 结构：上面是**可滚动**的内容区，下面是**固定**的页脚（保存/取消永远看得见）。
    # 之前是把所有东西都塞进一个 Frame，公司加多了窗口就顶出屏幕，「保存」直接看不见 —— 现在不会。
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    body = ttk.Frame(root, style="Card.TFrame")
    body.grid(row=0, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    canvas = tk.Canvas(body, bg=CARD_BG, highlightthickness=0, bd=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)

    frm = ttk.Frame(canvas, style="Card.TFrame", padding=(24, 20, 24, 18))
    win = canvas.create_window((0, 0), window=frm, anchor="nw")
    frm.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

    def _wheel(e) -> None:
        canvas.yview_scroll(-1 if getattr(e, "delta", 0) > 0 else 1, "units")

    root.bind_all("<MouseWheel>", _wheel)      # 指针在窗口内任意位置都能滚

    SIZING = {"ready": False, "max_ch": 0}

    def sync_scroll() -> None:
        """公司加/删之后重算：内容超高就露出滚动条、并让画布跟着伸缩。"""
        if not SIZING["ready"]:
            return
        try:
            root.update_idletasks()
            need_h = frm.winfo_reqheight()
            target = min(need_h, SIZING["max_ch"])
            if abs(int(float(canvas["height"])) - target) > 4:
                canvas.configure(height=target)
            if need_h > target + 2:
                if not vsb.winfo_ismapped():
                    vsb.grid(row=0, column=1, sticky="ns")
            elif vsb.winfo_ismapped():
                vsb.grid_remove()
        except Exception:  # noqa: BLE001
            pass

    frm.columnconfigure(1, weight=1, minsize=320)
    PAD = {"padx": 10, "pady": 7}

    # ── 金蝶连接 ──
    ttk.Label(frm, text="金蝶云星空", style="H.TLabel"
              ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 16))

    kd_rows = [
        ("base_url", "服务器地址", pre.get("base_url", "")),
        ("acct_name", "公司名 / 账套", acct_name),
        ("username", "用户名", pre.get("username", "")),
        ("password", "新密码" if change_key else "密码", ""),
    ]
    for i, (key, label, val) in enumerate(kd_rows, start=2):
        ttk.Label(frm, text=label).grid(row=i, column=0, sticky="e", **PAD)
        ttk.Entry(frm, textvariable=sv(key, str(val or "")), width=40,
                  show="*" if key == "password" else ""
                  ).grid(row=i, column=1, columnspan=2, sticky="we", **PAD)


    # ── 发票云 ──
    # 发票云的「组织」就是购方企业本身 —— 组织名自动取「购方企业全称」，界面不再问；
    # 也不维护「对应金蝶组织」：上传/建票时按**单据的组织名称**自动匹配
    # （见 piazzone_client.select_org(org_name=…)）。所以界面只留「一家一框」、无标题。
    pz_head = ttk.Frame(frm, style="Card.TFrame")
    pz_head.grid(row=7, column=0, columnspan=3, sticky="we", pady=(26, 4))
    pz_head.columnconfigure(0, weight=1)
    ttk.Label(pz_head, text="发票云（人人报销模块发票智慧管家收票、验票）", style="H.TLabel"
              ).grid(row=0, column=0, sticky="w")

    PZ_UI_FIELDS = [("ghf_mc", "购方企业全称"), ("tax_no", "购方税号"),
                    ("client_id", "client_id"), ("client_secret", "client_secret")]

    pz_host = ttk.Frame(frm)
    pz_host.grid(row=8, column=0, columnspan=3, sticky="we", pady=(6, 0))
    pz_host.columnconfigure(0, weight=1)
    blocks: list = []   # 每个元素 {frame: 卡片外框, vars: 字段, btn: 这张卡片自己的删除按钮}

    def sync_del_state() -> None:
        """只剩一家时把「−」置灰（删空了发票云配置就丢了）；「＋」始终可用。"""
        state = "normal" if len(blocks) > 1 else "disabled"
        for b in blocks:
            b["btn_del"].configure(state=state)

    def add_block(values=None) -> None:
        """再加一家公司。**不弹窗、不编标题** —— 组织名 = 购方企业全称，自动。"""
        values = values or {}
        # 一家 = 一个卡片框（外层 1px 边框 + 内层浅底），不用横线做分隔
        outer = ttk.Frame(pz_host, style="PZBorder.TFrame")
        outer.grid(row=len(blocks), column=0, sticky="we", pady=(0, 10))
        outer.columnconfigure(0, weight=1)
        box = ttk.Frame(outer, style="PZCard.TFrame", padding=(14, 12, 14, 12))
        box.grid(row=0, column=0, sticky="we", padx=1, pady=1)
        box.columnconfigure(1, weight=1)
        # 「＋ / －」单独占最上面一行、靠右，正好压在「购方企业全称」上方
        vs: dict = {}
        blk = {"frame": outer, "vars": vs, "btn_del": None}
        btns = ttk.Frame(box, style="PZCard.TFrame")
        btns.grid(row=0, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="＋", width=2, style="Mini.TButton",
                   command=lambda: add_block()).grid(row=0, column=0, padx=(0, 4))
        blk["btn_del"] = ttk.Button(btns, text="－", width=2, style="Mini.TButton",
                                    command=lambda: remove_block(blk))
        blk["btn_del"].grid(row=0, column=1)
        for i, (key, label) in enumerate(PZ_UI_FIELDS):
            ttk.Label(box, text=label, style="PZ.TLabel"
                      ).grid(row=i + 1, column=0, sticky="e", padx=(4, 10), pady=4)
            v = tk.StringVar(value=str(values.get(key) or ""))
            vs[key] = v
            ttk.Entry(box, textvariable=v, width=42,
                      show="*" if key == "client_secret" else ""
                      ).grid(row=i + 1, column=1, sticky="we", padx=(0, 4), pady=4)
        blocks.append(blk)
        sync_del_state()
        sync_scroll()

    def remove_block(blk) -> None:
        """删掉**指定的这一家**（按钮就在它自己的卡片最下面）。至少保留一个框。"""
        if len(blocks) <= 1 or blk not in blocks:
            return
        blk["frame"].destroy()
        blocks.remove(blk)
        for i, b in enumerate(blocks):        # 补上删掉后留下的行号空洞
            b["frame"].grid_configure(row=i)
        sync_del_state()
        sync_scroll()

    def collect_orgs() -> tuple:
        """界面 → `(orgs, active)`（组织名 = 购方企业全称，自动；逻辑在 `_orgs_from_rows`）。"""
        return _orgs_from_rows([{k: v.get() for k, v in b["vars"].items()} for b in blocks])

    for _n, _o in (pz_orgs or {}).items():
        add_block(dict(_o, ghf_mc=(_o.get("ghf_mc") or _n)))
    if not blocks:
        add_block()

    # ── 页脚（固定在 root 上，不随内容滚动）──
    def do_save() -> None:
        base_url = VAR["base_url"].get().strip()
        acct_name_v = VAR["acct_name"].get().strip()
        username = VAR["username"].get().strip()
        password = VAR["password"].get()
        if not (username and password):
            messagebox.showwarning("还差一点", "用户名和密码是必填项。", parent=root)
            return
        if not (base_url or acct_name_v):
            messagebox.showwarning("还差一点", "请填服务器地址或公司名。", parent=root)
            return

        pz_orgs_now, pz_active_now = collect_orgs()
        payload = {"base_url": base_url, "acct_name": acct_name_v,
                   "username": username, "password": password}
        if str(pre.get("acctid") or "").strip():
            payload["acctid"] = str(pre["acctid"]).strip()
        pz = _piazzone_payload(pz_orgs_now, pz_active_now)
        if pz:
            payload["piazzone"] = pz

        try:
            credstore.save(payload)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc), parent=root)
            return

        notes = ["凭据已加密保存。"]
        if pz:
            notes.append("发票云：已保存 %d 家。" % len(pz["orgs"]))
        if LEGACY_CONFIG.is_file():
            try:
                LEGACY_CONFIG.unlink()
                notes.append("旧明文文件已删除。")
            except OSError as exc:
                notes.append("⚠️ 明文文件删除失败，请手动删除：\n%s\n（%s）" % (LEGACY_CONFIG, exc))
        if legacy_has_secret:
            notes.append("提醒：请确认已在金蝶后台改掉旧密码。")
        messagebox.showinfo("完成", "\n".join(notes), parent=root)
        root.destroy()                      # 保存成功 → 自动关闭配置窗口

    # ── 固定页脚：只放「保存 / 取消」（右对齐；「删除 / 添加」在「发票云」那行）──
    # 页脚用**窗体底色**（灰），与白色内容区自然断开 —— 不再画线
    foot_wrap = ttk.Frame(root, style="Page.TFrame")
    foot_wrap.grid(row=1, column=0, sticky="we")
    foot_wrap.columnconfigure(0, weight=1)
    foot = ttk.Frame(foot_wrap, style="Page.TFrame", padding=(24, 14, 24, 16))
    foot.grid(row=0, column=0, sticky="we")
    foot.columnconfigure(0, weight=1)

    right = ttk.Frame(foot, style="Page.TFrame")
    right.grid(row=0, column=0, sticky="e")
    ttk.Button(right, text="保存", width=9, style="Primary.TButton",
               command=do_save).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(right, text="取消", width=9, command=root.destroy).grid(row=0, column=1)

    # 定尺寸：内容超高就给滚动条，页脚始终贴着底部、永远看得见
    try:
        root.update_idletasks()
        cw, ch = frm.winfo_reqwidth(), frm.winfo_reqheight()
        fh = foot_wrap.winfo_reqheight()
        SIZING["max_ch"] = max(root.winfo_screenheight() - fh - 130, 240)
        SIZING["ready"] = True
        if ch > SIZING["max_ch"]:
            canvas.configure(width=cw, height=SIZING["max_ch"])
            vsb.grid(row=0, column=1, sticky="ns")
        else:
            canvas.configure(width=cw, height=ch)
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        y = max(min((root.winfo_screenheight() - h) // 3,
                    root.winfo_screenheight() - h - 60), 12)
        root.geometry("+%d+%d" % (max((root.winfo_screenwidth() - w) // 2, 8), y))
    except Exception:  # noqa: BLE001
        pass

    root.mainloop()
    return 0


# ─────────────────────────────── CLI ───────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="金蝶凭据配置向导（弹窗、加密保存、不落明文）")
    ap.add_argument("--check", action="store_true", help="只打印状态，不开窗")
    ap.add_argument("--delete", action="store_true", help="删除已保存的加密凭据")
    ap.add_argument("--change-key", dest="change_key", action="store_true",
                    help="「修改密钥」模式开窗")
    a = ap.parse_args()

    if a.delete:
        print("已删除" if credstore.delete() else "（没有已保存的加密凭据）")
        return 0
    if a.check:
        data = credstore.load() or {}
        orgs, active = _piazzone_state(data.get("piazzone"))
        print("加密凭据文件 :", credstore.STORE_PATH, "存在" if credstore.exists() else "不存在")
        print("加密凭据内容 :", credstore.describe())
        if orgs:
            print("发票云组织   : %d 家（active=%s）" % (len(orgs), active or "-"))
            for _n, _v in orgs.items():
                print("               - %s ｜ 购方税号=%s"
                      % (_n, str(_v.get("tax_no") or "").strip() or "-"))
            if len(orgs) > 1:
                print("               （多家并存：上传/建票时按**单据的组织名称**自动匹配对应那家）")
        else:
            print("发票云组织   : 未配置（报销主链路不受影响）")
        has_plain = LEGACY_CONFIG.is_file() and str(_read_legacy_config().get("password") or "").strip()
        print("旧版明文配置 :", LEGACY_CONFIG, "存在（含明文密码）" if has_plain else "不存在")
        if has_plain:
            print("就绪状态     : ❌ 未迁移完成 —— 金蝶命令会被闸门阻断")
        elif data:
            print("就绪状态     : ✅ 已用加密凭据，闸门放行")
        else:
            print("就绪状态     : ⚠️ 尚无任何凭据，需先配置")
        return 0

    if not credstore.available():
        print("当前系统不支持 DPAPI（非 Windows）。请改用环境变量 KINGDEE_* / PIAZZONE_*。")
        return 2
    try:
        return run_gui(change_key=a.change_key)
    except ImportError:
        print("未找到 tkinter，无法弹窗。请改用环境变量方式注入凭据。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
