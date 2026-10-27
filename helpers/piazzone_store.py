# -*- coding: utf-8 -*-
"""发票云凭据的**独立**加密存储（DPAPI）—— `~/.workbuddy/kingdee/piazzone.bin`。

为什么单独一个文件
──────────────────
金蝶连接凭据（服务器/账套/用户名/密码）和发票云密钥（client_id / client_secret / 购方税号）
本来就不是一类东西：

  · 前者是**导出技能的唯一配置点**，全系列复用；
  · 后者只有报销助手在"验票 / 服务端上传发票"时才用，导出技能根本不碰。

以前两者挤在同一个 `credentials.bin` 里，结果是：用导出技能的向导改一次密码，就把它不认识的
`piazzone` 段整份覆盖掉 —— 发票云配置凭空消失，且没有任何提示。
分成两个文件后，**各自只有一个写入者**，物理上不可能互相踩。

三个约束（改动时别破坏）
────────────────────────
1. **本文件只由报销助手写**。导出技能不 import 它、不读 `piazzone.bin`。
2. `credentials.bin` 里的 `piazzone` 键属于**旧版本遗留**：读取时兼容，向导启动时自动迁移，
   迁移成功后从旧文件里删掉该键（不删整个文件，金蝶凭据还在里面）。
3. 迁移**先备份**再改写；`piazzone.bin` 已有数据时以它为准，绝不覆盖。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import credstore

PZ_PATH = credstore.STORE_DIR / "piazzone.bin"
# 复用 credstore 的 DPAPI 封装（含固定 entropy）。entropy 的作用只是让"顺手捡到密文的别的程序"
# 不能直接 CryptUnprotectData，不是密钥；两个文件共用一个 entropy 不降低防护强度。
_protect = credstore._protect
_unprotect = credstore._unprotect


def available() -> bool:
    return credstore.available()


def exists() -> bool:
    return PZ_PATH.is_file()


def save(payload: dict) -> None:
    """加密写入 piazzone.bin（原子替换；结构 `{"active": 名, "orgs": {名: {...}}}`）。"""
    if not available():
        raise RuntimeError("当前平台不支持 DPAPI，无法使用发票云凭据存储")
    credstore.STORE_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    blob = _protect(raw)
    tmp = PZ_PATH.with_suffix(".bin.tmp")
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, PZ_PATH)
    try:
        os.chmod(PZ_PATH, 0o600)
    except OSError:
        pass


def load() -> dict | None:
    """读并解密；无文件/解不开返回 None（解不开说明换了 Windows 用户或拷自别的机器）。"""
    if not available() or not exists():
        return None
    try:
        blob = PZ_PATH.read_bytes()
    except OSError:
        return None
    if not blob:
        return None
    try:
        raw = _unprotect(blob)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("[WARN] 发票云凭据无法解密（%s）—— 请重新运行凭据向导的发票云那一组" % exc)
        return None
    return data if isinstance(data, dict) else None


def delete() -> bool:
    if not exists():
        return False
    try:
        PZ_PATH.unlink()
        return True
    except OSError:
        return False


def migrate_from_credstore(quiet: bool = False) -> dict:
    """把 `credentials.bin` 里遗留的 `piazzone` 段搬到 `piazzone.bin`。

    返回 `{"migrated": bool, "reason": str}`。**幂等**：没有遗留数据时什么也不做。
    """
    try:
        data = credstore.load()
    except Exception:  # noqa: BLE001
        return {"migrated": False, "reason": "旧凭据读不出来"}
    if not isinstance(data, dict) or "piazzone" not in data:
        return {"migrated": False, "reason": "无遗留数据"}

    legacy = data.get("piazzone") or {}
    if not isinstance(legacy, dict) or not legacy:
        # 空壳也清掉，免得每次启动都判一次
        return _drop_key_from_credstore(data, quiet, "遗留段为空")

    if exists() and load():
        # 新文件已有数据 → 以它为准，只把旧文件里的键删掉
        return _drop_key_from_credstore(data, quiet, "piazzone.bin 已存在，保留新数据")

    try:
        save(legacy)
    except Exception as exc:  # noqa: BLE001
        return {"migrated": False, "reason": "写入 piazzone.bin 失败：%s" % exc}

    res = _drop_key_from_credstore(data, True, "")
    n = len((legacy.get("orgs") or {}))
    if not quiet:
        print("✓ 发票云凭据已迁到独立文件 piazzone.bin（%d 家）；旧文件已备份并移除该段。" % n)
    return {"migrated": True, "reason": "已迁移 %d 家" % n, "backup": res.get("backup", "")}


def _drop_key_from_credstore(data: dict, quiet: bool, note: str) -> dict:
    """从 credentials.bin 里删掉 `piazzone` 键（先备份），其余字段原样保留。"""
    backup = ""
    try:
        if credstore.STORE_PATH.is_file():
            backup = str(credstore.STORE_PATH) + ".bak-%d" % int(time.time())
            with open(credstore.STORE_PATH, "rb") as src, open(backup, "wb") as dst:
                dst.write(src.read())
    except OSError:
        backup = ""
    rest = {k: v for k, v in data.items() if k != "piazzone"}
    try:
        credstore.save(rest)
    except Exception as exc:  # noqa: BLE001
        return {"migrated": False, "reason": "清理旧段失败：%s" % exc, "backup": backup}
    if not quiet and note:
        print("（%s）" % note)
    return {"migrated": True, "reason": note, "backup": backup}
