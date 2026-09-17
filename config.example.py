# -*- coding: utf-8 -*-
"""金蝶云星空连接配置示例。

⚠️ 这是模板文件，会被分发 —— 不要把真实账号密码填在这里。
   要填真实凭据，请复制本文件为 config.py（同目录），或用下面推荐的方式。

推荐（技能升级/重装不会覆盖，也不会被误打包分发）：
    写 ~/.workbuddy/kingdee/config.json（Windows：C:\\Users\\<你>\\.workbuddy\\kingdee\\config.json）

    {
      "base_url": "https://你的域名/k3cloud/",
      "acct_name": "账套名称",
      "username": "取数账号",
      "password": "密码"
    }

    · acct_name 填账套名称即可，acctid 会自动解析；也可以直接填 acctid。
    · 这份配置与 kingdee-data-exporter / kingdee-data-analyzer 共用，只维护一份。
    · 多账套用 {"default": "A", "profiles": {"A": {...}, "B": {...}}}，
      并用环境变量 KINGDEE_PROFILE 切换。

配置完成后先自检（需要装 kingdee-data-exporter）：
    cd <kingdee-data-exporter 目录> && python data_exporter.py --doctor
"""

KINGDEE_CONFIG = {
    # 金蝶云星空地址，末尾通常是 /k3cloud/（漏了也会自动补全）
    "base_url": "https://your-kingdee-host/k3cloud/",
    # 账套 ID（可以不填，改用下面的 acct_name）
    "acctid": "your-acctid",
    # 账套名称（推荐：不用去问实施商要 acctid）
    "acct_name": "",
    # 集成用户（建议单独建一个只有报销相关权限的账号，不要用管理员）
    "username": "your-username",
    "password": "your-password",
}

# ── 也可以用环境变量，避免任何文件落地（优先级高于本文件）──
# KINGDEE_BASE_URL / KINGDEE_ACCTID / KINGDEE_ACCT_NAME
# / KINGDEE_USERNAME / KINGDEE_PASSWORD
