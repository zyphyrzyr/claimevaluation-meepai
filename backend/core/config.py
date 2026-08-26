"""
运行时配置 - Soft IP 主诉评估系统 v4
v2.1 修正：删除旧三维权重；对抗修正系数统一为 v4 的 0.7-1.3；案由扩为三类
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent   # backend/
PROJECT_DIR = BASE_DIR.parent                        # soft-ip-v4/
DATA_DIR = PROJECT_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
REPORT_DIR = RUNTIME_DIR / "reports"
DB_PATH = DATA_DIR / "soft_ip.db"
ENV_PATH = PROJECT_DIR / ".env"

APP_TITLE = "Soft IP 主诉评估系统"
APP_VERSION = "4.0.0"

# 案由（v4 扩为三类）
CAUSE_TRADEMARK = "商标侵权"
CAUSE_COPYRIGHT = "著作权侵权"
CAUSE_UNFAIR_COMPETITION = "不正当竞争"
SUPPORTED_CAUSE_TYPES = [CAUSE_TRADEMARK, CAUSE_COPYRIGHT, CAUSE_UNFAIR_COMPETITION]

# 业务目标
GOAL_TYPES = ["要钱", "要名"]

# 对抗检验修正系数范围（v4 统一：0.7-1.3，默认 1.0）
ADVERSARIAL_COEFF_MIN = 0.7
ADVERSARIAL_COEFF_MAX = 1.3
ADVERSARIAL_COEFF_DEFAULT = 1.0

# 权利基础红灯阈值
RIGHTS_RED_LINE = 60

# 决策分档
SCORE_THRESHOLD_GO = 75        # >= 75 建议优先启动
SCORE_THRESHOLD_PATCH = 60     # 60-74 补充短板后启动；< 60 暂缓

# 模型路由（§1）：强模型承担法律推理重的节点，普通模型承担结构化提取
LLM_STRONG_MODEL = "deepseek-reasoner"
LLM_FAST_MODEL = "deepseek-chat"
STRONG_MODEL_NODES = {"infringement", "judge"}   # 侵权认定、法官归纳

_RUNTIME_SETTING_KEYS = [
    "USE_MOCK",
    "LLM_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "QCC_API_TOKEN",
    "PKULAW_API_TOKEN",
    "SILICONFLOW_API_KEY",
]
_DEFAULTS = {
    "USE_MOCK": "True",
    "LLM_PROVIDER": "deepseek",
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "QCC_API_TOKEN": "",
    "PKULAW_API_TOKEN": "",
    "SILICONFLOW_API_KEY": "",
}


def _read_env_file() -> dict:
    values = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def get_runtime_settings() -> dict:
    file_values = _read_env_file()
    merged = dict(_DEFAULTS)
    for key in _RUNTIME_SETTING_KEYS:
        env_value = os.getenv(key)
        if env_value:
            merged[key] = env_value
    for key, value in file_values.items():
        if key in _RUNTIME_SETTING_KEYS:
            merged[key] = value

    return {
        "use_mock": str(merged["USE_MOCK"]).lower() == "true",
        "llm_provider": merged["LLM_PROVIDER"],
        "deepseek_api_key": merged["DEEPSEEK_API_KEY"].strip(),
        "deepseek_base_url": merged["DEEPSEEK_BASE_URL"].strip() or "https://api.deepseek.com",
        "qcc_api_token": merged["QCC_API_TOKEN"].strip(),
        "pkulaw_api_token": merged["PKULAW_API_TOKEN"].strip(),
        "siliconflow_api_key": merged["SILICONFLOW_API_KEY"].strip(),
    }


def ensure_dirs() -> None:
    for d in (DATA_DIR, RUNTIME_DIR, REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()
SQLITE_URL = f"sqlite:///{DB_PATH}"
