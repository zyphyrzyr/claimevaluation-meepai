"""
运行时配置 - Soft IP 主诉评估系统 v4
v2.1 修正：删除旧三维权重；对抗修正系数统一为 v4 的 0.7-1.3；案由扩为三类
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent   # backend/
PROJECT_DIR = BASE_DIR.parent                        # soft-ip-v4/

# 数据目录可用环境变量覆盖。测试必须指向临时目录：
# SQLite 与向量库都是共享持久化的，若测试与本地 dev 服务共用同一份数据，
# E2E 写入的知识条目会污染测试结果（曾导致知识库用例在跑完 E2E 后失败）。
DATA_DIR = Path(os.environ["SOFT_IP_DATA_DIR"]) if os.environ.get("SOFT_IP_DATA_DIR") \
    else PROJECT_DIR / "data"
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

# 决策分档（v2 校准方案：78 / 62，原 75 / 60）
SCORE_THRESHOLD_GO = 78        # >= 78 建议优先启动
SCORE_THRESHOLD_PATCH = 62     # 62-77 补充短板后启动；< 62 暂缓

# 四象限图中线（法律轴 / 业务轴各自的中线）
# 语义上不同于上面的「总分档位线」，两者独立命名，不得合并。
# 取值与 SCORE_THRESHOLD_GO 对齐：幂平均恒有 min(x,y) <= M_p(x,y) <= max(x,y)，
# 故两轴均 >= 78 时总分必 >= 78，可杜绝「四象限说强推起诉、决策卡片说补短板」的同屏矛盾。
QUADRANT_AXIS_MID = 78

# 评分聚合方式：幂平均（广义平均）M_p = ((1/n)·Σ xᵢᵖ)^(1/p)
#   p = 1    算术平均，不惩罚短板
#   p = 0    几何平均，温和惩罚
#   p = -0.5 中等惩罚（当前采用）
#   p = -1   调和平均，严厉惩罚
#   p -> -∞  趋近取最小值
# p <= 0 时任一维度 <= 0 则聚合结果归零，即「一票否决」语义。
# 接真实模型后可按实际分布微调，调参只动这一个值。
POWER_MEAN_P = -0.5

# 模型路由（§1）：强模型承担法律推理重的节点，普通模型承担结构化提取
LLM_STRONG_MODEL = "deepseek-reasoner"
LLM_FAST_MODEL = "deepseek-chat"
STRONG_MODEL_NODES = {"infringement", "judge"}   # 侵权认定、法官归纳

# 单节点超时（秒）。7 个节点串行执行，超时过长会让整轮评估卡死：
# 原本是 180s，最坏情况一轮要 20 分钟，用户只会以为系统挂了。
LLM_TIMEOUT_SECONDS = 90

# 哪些模型支持 response_format=json_object。
# deepseek-reasoner 明确不支持该参数（连同 temperature / function calling 一并忽略），
# 对它下发会直接返回 400——所以 json 模式必须按模型判断，不能全局开。
JSON_MODE_MODELS = {"deepseek-chat"}

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

    settings = {
        "use_mock": str(merged["USE_MOCK"]).lower() == "true",
        "llm_provider": merged["LLM_PROVIDER"],
        "deepseek_api_key": merged["DEEPSEEK_API_KEY"].strip(),
        "deepseek_base_url": merged["DEEPSEEK_BASE_URL"].strip() or "https://api.deepseek.com",
        "qcc_api_token": merged["QCC_API_TOKEN"].strip(),
        "pkulaw_api_token": merged["PKULAW_API_TOKEN"].strip(),
        "siliconflow_api_key": merged["SILICONFLOW_API_KEY"].strip(),
    }

    # 同时挂一份大写键：.env 里写的是 USE_MOCK / DEEPSEEK_API_KEY，
    # 而现有调用方一律用小写。只提供小写的话，照 .env 的写法访问会静默拿到
    # None——不报错，但配置等于没生效（写探针脚本时就踩了这一脚）。
    for key, value in list(settings.items()):
        settings[key.upper()] = value
    return settings


def ensure_dirs() -> None:
    for d in (DATA_DIR, RUNTIME_DIR, REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()
SQLITE_URL = f"sqlite:///{DB_PATH}"
