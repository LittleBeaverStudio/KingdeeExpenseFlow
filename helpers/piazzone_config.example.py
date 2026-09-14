# -*- coding: utf-8 -*-
"""发票云(piazzone)接入凭证示例 —— 可选兜底，金蝶报销主链路不需要它。

⚠️ 只在「发票不在收票池、需要程序化采集/绑定」时才用得上。
   金蝶标准的「收票信息」写入走 helpers/recvin_link.py，纯金蝶 WebAPI，不需要发票云授权。

用法：复制本文件为 piazzone_config.py 并填写自己的凭证。
piazzone_config.py 已在 .gitignore 中，不会被提交。
"""

ACTIVE_ENV = "prod"

ENV = {
    "prod": {
        "base_url": "https://api.piazzone.com",
        "tax_no": "请填写购方统一社会信用代码",
        "ghf_mc": "请填写购方企业全称",
        "client_id": "请填写你的client_id",
        "client_secret": "请填写你的client_secret",
        "encrypt_key": "请填写你的encrypt_key",
        "enc_type": 0,   # 0=MD5（官方示例统一使用）
    },
    "dev": {
        "base_url": "https://api-dev.piaozone.com/test",
        "tax_no": "请填写测试购方税号",
        "ghf_mc": "请填写测试购方名称",
        "client_id": "请填写测试环境client_id",
        "client_secret": "请填写测试环境client_secret",
        "encrypt_key": "",
        "enc_type": 0,
    },
}

import os

_cfg = ENV[ACTIVE_ENV]
API_BASE = os.environ.get("PIAZZONE_BASE_URL", _cfg["base_url"])
TAX_NO = _cfg["tax_no"]
GHF_MC = _cfg["ghf_mc"]
CLIENT_ID = _cfg["client_id"]
CLIENT_SECRET = _cfg["client_secret"]
ENCRYPT_KEY = _cfg["encrypt_key"]
ENC_TYPE = _cfg["enc_type"]
