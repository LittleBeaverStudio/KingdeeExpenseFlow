# -*- coding: utf-8 -*-
"""金蝶云星空连接配置示例。

用法：把本文件复制为 config.py（同目录），填入你自己的信息。
config.py 已在 .gitignore 中，不会被提交；请勿把真实账套信息推到公开仓库。

本 Skill 与 kingdee-data-exporter / kingdee-data-analyzer 共用同一份 config.py，
三者放在同一父目录时可只维护一份（或用 KINGDEE_* 环境变量）。
"""

KINGDEE_CONFIG = {
    # 金蝶云星空地址，末尾通常是 /k3cloud/
    "base_url": "https://your-kingdee-host/k3cloud/",
    # 账套 ID
    "acctid": "your-acctid",
    # 集成用户（建议单独建一个只有报销相关权限的账号，不要用管理员）
    "username": "your-username",
    "password": "your-password",
}

# ── 也可以用环境变量，避免任何文件落地 ──
# KINGDEE_BASE_URL / KINGDEE_ACCTID / KINGDEE_USERNAME / KINGDEE_PASSWORD
# 环境变量优先级高于本文件。
