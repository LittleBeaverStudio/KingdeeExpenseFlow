# -*- coding: utf-8 -*-
"""金蝶凭据配置向导（弹窗 / 文字向导）—— **明文不落盘**。

> 本文件是 **kingdee-expense-flow（报销助手）专版**，比导出/分析技能的版本多
> 「发票云」一组（且支持**多组织**）。导出 / 分析技能不需要发票云，它们的同名脚本不含该组。

三条硬规矩
──────────
1. **不经过对话**：凭据只在本地窗口里输入，不进任何聊天记录。
2. **不落明文**：敏感字段经 DPAPI 加密写入 `~/.workbuddy/kingdee/credentials.bin`。
3. **不回显、不写日志**：输入框掩码；只打印字段名是否存在，永不打印值。

用法
────
  python helpers/setup_credentials.py              # 配置窗口
  python helpers/setup_credentials.py --change-key # 「修改密钥」模式
  python helpers/setup_credentials.py --check      # 只看状态，不开窗
  python helpers/setup_credentials.py --delete     # 清除已保存凭据
  python helpers/setup_credentials.py --console    # 强制文字向导（排障用）

> **用户不需要打开这个 .py，也不需要敲命令** —— 由智能体代为调用，窗口弹在用户屏幕上。

退出码（**agent 必须按码判断结果，不要看有没有报错**）
──────────────────────────────────────────────────
  0   已加密保存，可以继续跑业务命令
  10  用户取消 / 关窗 / 超时（**没改成**，不要盲目重跑）
  12  已经有一个配置窗口开着（等那个窗填完，别开第二个）
  2   本机环境不支持（非 Windows / 无 DPAPI / 图形与文字界面都不可用）
  3   保存失败（窗口里已提示原因）
  4   图形界面不可用，需要降级（内部用，正常不会作为最终结果返回）

界面降级顺序（**零安装**，绝不静默装任何东西）
──────────────────────────────────────────
  L0 本进程有 tkinter → 直接弹窗
  L1 本进程没有 → 到本机找**带 tkinter 的其它 Python**（py 启动器 / 注册表 / 安装目录），
     由它以 `--ui-child` 子进程弹窗。用户无感，也不改系统 PATH。
  L2 一个都没有 → `--console-child` 在**新控制台窗口**里跑文字向导。
     ⚠️ 新控制台是关键：密码输在那个窗口自己的 stdin，**不进 agent 的管道**，
     所以不会出现在对话记录里。绝不能在原进程里 input()。
  L3 都不行 → 如实回报，给「用户点确认后才协助安装」的可选动作；**不做静默安装**。

⚠️ 实现注意（踩过的坑）
──────────────────────
`ttk.Entry` **不会**持有 `StringVar` 的 Python 引用。若在循环里 `var = tk.StringVar()`
逐轮重新赋值，上一轮的 StringVar 会被 GC，而 `tkinter.Variable.__del__` 会 unset 掉
Tcl 变量 → **输入框被清空、预填全部丢失**（实测四个框全空）。
所以本文件用 `VAR` 字典**持有所有 StringVar 的引用**，窗口不关就不释放。
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import credstore  # noqa: E402

LEGACY_CONFIG = Path.home() / ".workbuddy" / "kingdee" / "config.json"
PIAZZONE_DEFAULT_BASE = "https://api.piaozone.com"

# ── 退出码（agent 按码判断，别按输出文字猜）──
EXIT_OK = 0
EXIT_CANCELLED = 10
EXIT_BUSY = 12
EXIT_UNSUPPORTED = 2
EXIT_SAVE_FAILED = 3
EXIT_NEED_FALLBACK = 4          # 内部信号：图形界面不可用

# 窗口无操作自动关闭（毫秒）——用户走开后后台进程不会一直挂着
IDLE_TIMEOUT_MS = 20 * 60 * 1000

LOCK_PATH = credstore.STORE_DIR / ".setup.lock"

_EXPORTER_CANDIDATES = (
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent.parent,
    Path.home() / ".workbuddy" / "skills" / "kingdee-data-exporter",
    Path.home() / ".workbuddy" / "skills" / "KingdeeDataExporter",
)


class _NoUI(Exception):
    """图形界面建不起来（没有 tkinter、TclError、无桌面会话）。"""


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


# ─────────────── 单实例锁：别让用户看到两个配置窗 ───────────────

# 用**操作系统级文件锁**（Windows `msvcrt.locking` / POSIX `flock`），不用 PID 探测：
# 进程一退出内核自动释放，**不存在"陈旧锁把用户挡在门外"**。锁文件本身留作长期令牌，不删。
_LOCK_FH = None


def _acquire_lock() -> bool:
    """拿单实例锁；拿不到说明已经有一个配置窗口开着。锁不住文件时不拦用户。"""
    global _LOCK_FH
    try:
        credstore.STORE_DIR.mkdir(parents=True, exist_ok=True)
        fh = open(LOCK_PATH, "a+")
    except OSError:
        return True
    try:
        try:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    try:                        # 只作排查线索，读不到也无所谓
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _LOCK_FH = fh
    return True


def _release_lock() -> None:
    global _LOCK_FH
    if _LOCK_FH is None:
        return
    try:
        _LOCK_FH.seek(0)
        try:
            import msvcrt
            msvcrt.locking(_LOCK_FH.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(_LOCK_FH.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _LOCK_FH.close()
    except OSError:
        pass
    _LOCK_FH = None


# ─────────────── 找一台机器上「带 tkinter 的 Python」（零安装） ───────────────

_PROBE_CODE = ("import sys, tkinter; "
               "sys.exit(0 if sys.version_info >= (3, 8) and tkinter.TkVersion else 1)")


def _has_tkinter() -> bool:
    try:
        import tkinter  # noqa: F401
        return bool(tkinter.TkVersion)
    except Exception:  # noqa: BLE001
        return False


def _probe(prefix: list[str]) -> bool:
    try:
        res = subprocess.run(prefix + ["-c", _PROBE_CODE],
                             capture_output=True, timeout=10)
        return res.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _registry_pythons() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    out: list[str] = []
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Python\PythonCore"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Python\PythonCore"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Python\PythonCore")]
    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as key:
                count = winreg.QueryInfoKey(key)[0]
                tags = [winreg.EnumKey(key, i) for i in range(count)]
        except OSError:
            continue
        for tag in tags:
            try:
                with winreg.OpenKey(hive, base + "\\" + tag + "\\InstallPath") as ik:
                    val, _ = winreg.QueryValueEx(ik, "")
            except OSError:
                continue
            path = str(val or "").strip()
            if not path:
                continue
            exe = path if path.lower().endswith(".exe") else os.path.join(path, "python.exe")
            out.append(exe)
    return out


def _iter_python_candidates():
    """产出「命令前缀列表」：普通解释器是 [exe]，py 启动器是 [py, -3.11]。"""
    seen: set[str] = set()
    me = os.path.normcase(os.path.abspath(sys.executable))

    def fresh(path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        key = os.path.normcase(os.path.abspath(path))
        if key == me or key in seen:
            return False
        seen.add(key)
        return True

    # ① py 启动器：-0p 列出已注册版本；路径解析不出来就退化成 `py -X.Y`
    launcher = shutil.which("py")
    if launcher:
        try:
            listed = subprocess.run([launcher, "-0p"], capture_output=True,
                                    text=True, timeout=10).stdout or ""
        except Exception:  # noqa: BLE001
            listed = ""
        for line in listed.splitlines():
            m = re.search(r"([A-Za-z]:\\[^\r\n]*?pythonw?\.exe)\s*$", line.strip())
            if m and fresh(m.group(1)):
                yield [m.group(1)]
        for tag in re.findall(r"-V:(\d+\.\d+)", listed):
            yield [launcher, "-" + tag]

    # ② 注册表登记的安装位置（官方安装包一定写这里）
    for path in _registry_pythons():
        if fresh(path):
            yield [path]

    # ③ 常见安装目录
    patterns = [r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe",
                r"%PROGRAMFILES%\Python3*\python.exe",
                r"%PROGRAMFILES(X86)%\Python3*\python.exe",
                r"C:\Python3*\python.exe"]
    for pat in patterns:
        expanded = os.path.expandvars(pat)
        if "%" in expanded:                     # 变量不存在就跳过，别去 glob 字面量
            continue
        for path in sorted(glob.glob(expanded), reverse=True):
            if fresh(path):
                yield [path]

    # ④ PATH 上的 python3 / python（当前那个没 tkinter，会被去重挡掉）
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path and fresh(path):
            yield [path]


def _find_tkinter_python(limit: int = 6) -> list[str] | None:
    """返回能弹窗的解释器命令前缀；找不到返回 None。最多探 limit 个，避免拖慢。"""
    tried = 0
    for prefix in _iter_python_candidates():
        tried += 1
        if tried > limit:
            break
        if _probe(prefix):
            return prefix
    return None


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


def _save_payload(payload: dict) -> tuple[bool, str, list[str]]:
    """统一落盘：加密保存 → 删旧明文 → 给用户看的提示行。"""
    try:
        credstore.save(payload)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), []
    notes = ["凭据已加密保存。"]
    if payload.get("piazzone"):
        notes.append("发票云：已保存 %d 家。" % len(payload["piazzone"]["orgs"]))
    if LEGACY_CONFIG.is_file():
        try:
            LEGACY_CONFIG.unlink()
            notes.append("旧明文文件已删除。")
        except OSError as exc:
            notes.append("⚠️ 明文文件删除失败，请手动删除：\n%s\n（%s）" % (LEGACY_CONFIG, exc))
    return True, "", notes


# ─────────────────────────────── 界面 ───────────────────────────────

def run_gui(change_key: bool = False) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:  # noqa: BLE001
        raise _NoUI("没有 tkinter（%s）" % exc) from exc

    if not _acquire_lock():
        print("已经有一个凭据配置窗口开着。请在那个窗口里填完，不要再开第二个。")
        return EXIT_BUSY

    try:
        return _run_gui_inner(change_key, tk, messagebox, ttk)
    finally:
        _release_lock()


def _run_gui_inner(change_key: bool, tk, messagebox, ttk) -> int:
    pre = collect_prefill()
    legacy_has_secret = bool(str(_read_legacy_config().get("password") or "").strip())
    pz_orgs, _pz_active = _piazzone_state(pre.get("piazzone"))

    # 账套名：能取就补上（免认证接口，失败就留空，不影响保存）
    acct_name = str(pre.get("acct_name") or "").strip()
    if not acct_name and pre.get("acctid"):
        acct_name = _lookup_acct_name(pre.get("base_url"), pre.get("acctid"))

    try:
        root = tk.Tk()
    except Exception as exc:  # noqa: BLE001  TclError / 无桌面会话
        raise _NoUI("窗口创建失败（%s）" % exc) from exc

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

    # 结果：0=已保存；10=取消/关窗/超时。mainloop 结束后按它返回。
    RESULT = {"code": None}

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
    ]
    for i, (key, label, val) in enumerate(kd_rows, start=2):
        ttk.Label(frm, text=label).grid(row=i, column=0, sticky="e", **PAD)
        ttk.Entry(frm, textvariable=sv(key, str(val or "")), width=40
                  ).grid(row=i, column=1, columnspan=2, sticky="we", **PAD)

    # ── 密码行：带「显示」勾选（打错的代价是撞锁号计数，必须让人能核对） ──
    pwd_row = 2 + len(kd_rows)
    ttk.Label(frm, text="新密码" if change_key else "密码"
              ).grid(row=pwd_row, column=0, sticky="e", **PAD)
    pwd_entry = ttk.Entry(frm, textvariable=sv("password", ""), width=40, show="*")
    pwd_entry.grid(row=pwd_row, column=1, sticky="we", **PAD)
    show_pwd = tk.BooleanVar(value=False)

    def _toggle_pwd() -> None:
        pwd_entry.configure(show="" if show_pwd.get() else "*")
        if change_key and pwd2_entry is not None:
            pwd2_entry.configure(show="" if show_pwd.get() else "*")

    ttk.Checkbutton(frm, text="显示", variable=show_pwd, command=_toggle_pwd
                    ).grid(row=pwd_row, column=2, sticky="w", padx=(0, 4))

    # ── 改密钥时要二次确认：后台密码已改、本地打错的后果是必然登录失败 ──
    pwd2_entry = None
    if change_key:
        ttk.Label(frm, text="再输一次").grid(row=pwd_row + 1, column=0, sticky="e", **PAD)
        pwd2_entry = ttk.Entry(frm, textvariable=sv("password2", ""), width=40, show="*")
        pwd2_entry.grid(row=pwd_row + 1, column=1, columnspan=2, sticky="we", **PAD)

    # ── 发票云 ──
    # 发票云的「组织」就是购方企业本身 —— 组织名自动取「购方企业全称」，界面不再问；
    # 也不维护「对应金蝶组织」：上传/建票时按**单据的组织名称**自动匹配
    # （见 piazzone_client.select_org(org_name=…)）。所以界面只留「一家一框」、无标题。
    pz_head_row = pwd_row + (2 if change_key else 1) + 1
    pz_head = ttk.Frame(frm, style="Card.TFrame")
    pz_head.grid(row=pz_head_row, column=0, columnspan=3, sticky="we", pady=(26, 4))
    pz_head.columnconfigure(0, weight=1)
    ttk.Label(pz_head, text="发票云（人人报销模块发票智慧管家收票、验票）", style="H.TLabel"
              ).grid(row=0, column=0, sticky="w")

    PZ_UI_FIELDS = [("ghf_mc", "购方企业全称"), ("tax_no", "购方税号"),
                    ("client_id", "client_id"), ("client_secret", "client_secret")]

    pz_host = ttk.Frame(frm)
    pz_host.grid(row=pz_head_row + 1, column=0, columnspan=3, sticky="we", pady=(6, 0))
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

    # ── 脏检查用的快照（关窗前判断「有没有填过东西」）──
    def _snapshot() -> tuple:
        parts = [v.get() for v in VAR.values()]
        for b in blocks:
            parts.extend(v.get() for v in b["vars"].values())
        return tuple(parts)

    initial = _snapshot()

    # ── 页脚（固定在 root 上，不随内容滚动）──
    def do_save() -> None:
        base_url = VAR["base_url"].get().strip()
        acct_name_v = VAR["acct_name"].get().strip()
        username = VAR["username"].get().strip()
        password = VAR["password"].get()
        if not (username and password):
            messagebox.showwarning("还差一点", "用户名和密码是必填项。", parent=root)
            return
        if change_key and password != VAR["password2"].get():
            messagebox.showwarning("两次不一致", "两次输入的密码不一样，请核对后再保存。",
                                   parent=root)
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

        ok, err, notes = _save_payload(payload)
        if not ok:
            messagebox.showerror("保存失败", err, parent=root)
            return
        if legacy_has_secret:
            notes.append("提醒：请确认已在金蝶后台改掉旧密码。")
        messagebox.showinfo("完成", "\n".join(notes), parent=root)
        RESULT["code"] = EXIT_OK
        root.destroy()

    def on_close() -> None:
        """关窗 / 取消 / 超时统一出口：有改动才问一句，然后返回 10（= 没改成）。"""
        if RESULT["code"] == EXIT_OK:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            if _snapshot() != initial:
                if not messagebox.askyesno("还没保存",
                                           "填写的内容还没保存，确定关闭吗？",
                                           default="no", parent=root):
                    return
        except Exception:  # noqa: BLE001
            pass
        RESULT["code"] = EXIT_CANCELLED
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)

    # ── 空闲看门狗：用户走开后不会一直挂着一个后台进程 ──
    watchdog = {"id": None}

    def _arm() -> None:
        if watchdog["id"] is not None:
            try:
                root.after_cancel(watchdog["id"])
            except Exception:  # noqa: BLE001
                pass
        watchdog["id"] = root.after(IDLE_TIMEOUT_MS, _expire)

    def _expire() -> None:
        watchdog["id"] = None
        try:
            messagebox.showinfo("已自动关闭",
                                "窗口 20 分钟没有操作，已自动关闭（没有保存任何内容）。\n"
                                "需要时跟我说一声，我再帮你打开。", parent=root)
        except Exception:  # noqa: BLE001
            pass
        RESULT["code"] = EXIT_CANCELLED
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    root.bind_all("<Key>", lambda _e: _arm(), add="+")
    root.bind_all("<Button-1>", lambda _e: _arm(), add="+")
    _arm()

    # ── 固定页脚：只放「保存 / 取消」（右对齐；「删除 / 添加」在「发票云」那行）──
    # 页脚用**窗体底色**（灰），与白色内容区自然断开 —— 不再画线
    foot_wrap = ttk.Frame(root, style="Page.TFrame")
    foot_wrap.grid(row=1, column=0, sticky="we")
    foot_wrap.columnconfigure(0, weight=1)
    foot = ttk.Frame(foot_wrap, style="Page.TFrame", padding=(24, 14, 24, 16))
    foot.grid(row=0, column=0, sticky="we")
    foot.columnconfigure(0, weight=1)

    # 左：一句"为什么是弹窗"，让用户明白不是网站在要密码
    ttk.Label(foot, text="凭据在本机加密保存，不经对话框、不进日志",
              style="Page.TLabel", font=(FS, 9)
              ).grid(row=0, column=0, sticky="w")

    right = ttk.Frame(foot, style="Page.TFrame")
    right.grid(row=0, column=1, sticky="e")
    ttk.Button(right, text="保存", width=9, style="Primary.TButton",
               command=do_save).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(right, text="取消", width=9, command=on_close).grid(row=0, column=1)

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

    try:
        root.lift()
        root.focus_force()
    except Exception:  # noqa: BLE001
        pass

    root.mainloop()
    return RESULT["code"] if RESULT["code"] is not None else EXIT_CANCELLED


# ────────────────────────── 文字向导（无 tkinter 时的降级） ──────────────────────────

def _console_wizard(change_key: bool = False) -> int:
    """在新控制台窗口里跑的文字向导。

    ⚠️ 必须由 `--console-child` 在**独立控制台**里启动：密码走那个窗口自己的 stdin，
    不会经过 agent 的管道，所以不会出现在对话记录里。
    """
    pre = collect_prefill()
    legacy_has_secret = bool(str(_read_legacy_config().get("password") or "").strip())
    pz_orgs, _active = _piazzone_state(pre.get("piazzone"))

    print("=" * 64)
    print("金蝶云星空 · 凭据录入（文字向导）")
    print("在这里输入的密码不会出现在对话里，也不会以明文存到磁盘。")
    print("直接关掉这个窗口 = 放弃，不会保存任何内容。")
    print("=" * 64)

    def ask(label: str, default: str = "", secret: bool = False) -> str | None:
        while True:
            hint = "" if (secret or not default) else " [回车沿用 %s]" % default
            try:
                if secret:
                    val = getpass.getpass("  %s: " % label)
                else:
                    val = input("  %s%s: " % (label, hint))
            except (EOFError, KeyboardInterrupt):
                return None
            val = val if secret else val.strip()
            if secret:
                if val:
                    return val
            else:
                if val:
                    return val
                if default:
                    return default
            print("    ↑ 这一项必填。")

    base_url = ask("服务器地址", str(pre.get("base_url") or ""))
    if base_url is None:
        print("\n已取消，未保存。")
        return EXIT_CANCELLED
    acct_name = ask("公司名 / 账套", str(pre.get("acct_name") or ""))
    if acct_name is None:
        print("\n已取消，未保存。")
        return EXIT_CANCELLED
    username = ask("用户名", str(pre.get("username") or ""))
    if username is None:
        print("\n已取消，未保存。")
        return EXIT_CANCELLED
    password = ask("新密码" if change_key else "密码", secret=True)
    if password is None:
        print("\n已取消，未保存。")
        return EXIT_CANCELLED
    again = ask("再输一次（防打错）", secret=True)
    if again is None:
        print("\n已取消，未保存。")
        return EXIT_CANCELLED
    if password != again:
        print("\n两次输入不一致，未保存。请重新运行再填一次。")
        return EXIT_CANCELLED

    payload = {"base_url": base_url, "acct_name": acct_name,
               "username": username, "password": password}
    if str(pre.get("acctid") or "").strip():
        payload["acctid"] = str(pre["acctid"]).strip()

    # 发票云（可选）
    saved_orgs = dict(pz_orgs or {})
    if saved_orgs:
        print("\n发票云：当前已保存 %d 家。" % len(saved_orgs))
        ans = input("  要重新维护这一组吗？(y/N): ").strip().lower()
    else:
        ans = input("\n发票云（可选，仅验票/服务端上传才需要）现在配置吗？(y/N): ").strip().lower()

    rows: list = []
    if ans == "y":
        print("  一家一家填，购方企业全称留空表示结束。")
        while True:
            name = input("    购方企业全称（留空结束）: ").strip()
            if not name:
                break
            tax_no = input("    购方税号: ").strip()
            cid = input("    client_id: ").strip()
            csec = getpass.getpass("    client_secret: ")
            rows.append({"ghf_mc": name, "tax_no": tax_no,
                         "client_id": cid, "client_secret": csec})
        orgs, active = _orgs_from_rows(rows)
        pz = _piazzone_payload(orgs, active)
        if pz:
            payload["piazzone"] = pz
    elif saved_orgs:
        pz = _piazzone_payload(saved_orgs, _active)
        if pz:
            payload["piazzone"] = pz

    ok, err, notes = _save_payload(payload)
    if not ok:
        print("\n❌ 保存失败：%s" % err)
        return EXIT_SAVE_FAILED
    if legacy_has_secret:
        notes.append("提醒：请确认已在金蝶后台改掉旧密码。")
    print("")
    for line in notes:
        print("✓ " + line)
    print("✓ 就绪状态：已用加密凭据，闸门放行")
    try:      # 独立控制台会随进程退出而关闭，停一下让用户看清结果
        input("\n按回车关闭这个窗口…")
    except (EOFError, KeyboardInterrupt):
        pass
    return EXIT_OK


# ─────────────────────────────── 启动器 ───────────────────────────────

def _try_ui(change_key: bool) -> int | None:
    """弹窗；建不起来（或中途异常）返回 None，交给上层降级 —— 绝不把 traceback 喷给用户。"""
    try:
        return run_gui(change_key=change_key)
    except _NoUI as exc:
        print("图形界面打不开：%s" % exc)
        return None
    except Exception as exc:  # noqa: BLE001
        print("图形界面异常（%s: %s），改用其它方式。" % (type(exc).__name__, exc))
        return None


def _relaunch(prefix: list[str], flag: str, change_key: bool) -> int:
    """用另一个解释器/模式重启自己，并把退出码原样带回。"""
    script = str(Path(__file__).resolve())
    cmd = list(prefix) + [script, flag] + (["--change-key"] if change_key else [])
    flags = 0
    if flag == "--console-child":
        # 关键：独立控制台 → 子进程的 stdin/stdout 都不接 agent 的管道
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(cmd, creationflags=flags, timeout=40 * 60)
    except subprocess.TimeoutExpired:
        print("录入窗口超过 40 分钟未完成，已放弃（没有保存任何内容）。")
        return EXIT_CANCELLED
    except OSError as exc:
        print("启动 %s 失败：%s" % (" ".join(cmd), exc))
        return EXIT_NEED_FALLBACK
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="金蝶凭据配置向导（弹窗、加密保存、不落明文）")
    ap.add_argument("--check", action="store_true", help="只打印状态，不开窗")
    ap.add_argument("--delete", action="store_true", help="删除已保存的加密凭据")
    ap.add_argument("--change-key", dest="change_key", action="store_true",
                    help="「修改密钥」模式开窗")
    ap.add_argument("--console", action="store_true", help="强制文字向导（排障用）")
    ap.add_argument("--ui-child", dest="ui_child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--console-child", dest="console_child", action="store_true",
                    help=argparse.SUPPRESS)
    a = ap.parse_args()

    if a.delete:
        print("已删除" if credstore.delete() else "（没有已保存的加密凭据）")
        return EXIT_OK

    if a.check:
        data = credstore.load() or {}
        orgs, active = _piazzone_state(data.get("piazzone"))
        print("加密凭据文件 :", credstore.STORE_PATH, "存在" if credstore.exists() else "不存在")
        print("加密凭据内容 :", credstore.describe())
        print("图形界面     :", "可用" if _has_tkinter() else "当前解释器没有 tkinter（会自动另找）")
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
        return EXIT_OK

    if not credstore.available():
        print("本机不是 Windows（或缺少 DPAPI），无法加密保存凭据，因此不提供录入窗口。")
        print("可选：① 在 Windows 电脑上使用本技能（推荐）；")
        print("      ② 临时用环境变量 KINGDEE_* —— 注意环境变量在本机并不保密。")
        return EXIT_UNSUPPORTED

    # 子进程入口：只跑指定界面，不再探测，避免无限递归
    if a.ui_child:
        code = _try_ui(a.change_key)
        return EXIT_NEED_FALLBACK if code is None else code
    if a.console_child or a.console:
        return _console_wizard(change_key=a.change_key)

    # L0：当前解释器就能弹窗
    if _has_tkinter():
        code = _try_ui(a.change_key)
        if code is not None:
            return code

    # L1：找本机带 tkinter 的 Python（零安装，不改系统环境）
    prefix = _find_tkinter_python()
    if prefix:
        print("当前解释器没有 tkinter，改用本机已装的 Python 打开录入窗口：%s"
              % " ".join(prefix))
        code = _relaunch(prefix, "--ui-child", a.change_key)
        if code != EXIT_NEED_FALLBACK:
            return code

    # L2：文字向导（独立控制台，密码不进对话）
    print("本机没有可用的图形界面，改用文字向导：马上会弹出一个黑色的录入窗口，")
    print("请在**那个窗口**里按提示填写（密码不会出现在这里）。")
    return _relaunch([sys.executable], "--console-child", a.change_key)


if __name__ == "__main__":
    raise SystemExit(main())
