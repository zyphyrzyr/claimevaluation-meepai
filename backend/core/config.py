"""
运行时配置 - Soft IP 主诉评估系统 v4
v2.1 修正：删除旧三维权重；对抗修正系数统一为 v4 的 0.7-1.3；案由扩为三类
"""

import os
from pathlib import Path

from .providers import (ALL_KEY_ENV_NAMES, DEFAULT_PROVIDER_ID, get_preset,
                        resolve_api_key)

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

# 评估启动时的后台自动召回（方案 B：材料注入完全后台化，用户无感）
# - 查询词 = 案由 + 业务目标 + 案情描述（确定性拼接，不用 LLM）
# - 双库检索（本案材料库 + 全局经验库，防污染规则内建于 search_knowledge）
# - 只保留 score >= 合格线的命中，注入快照照常写入审计；合格线由
#   auto_recall_min_score() 按向量化后端给出（哈希兜底 vs bge-m3 两套刻度）
AUTO_RECALL_TOP_K = int(os.environ.get("AUTO_RECALL_TOP_K", "8"))

# 合格线**随后端自适应**，不能两后端共用一条线——
# 哈希兜底向量按字符二元组统计，余弦相似度系统性偏低（实测相关文本仅 0.13-0.61），
# 沿用 bge-m3 标定的 0.3 会让**全局经验库的短条目（20-40 字）全军覆没**：
# 实测 4 条全落在 0.13-0.23，一条都进不了注入集，「经验沉淀」在演示里等于没生效，
# 而界面只显示「自动召回 0 条」，与空库无从区分。长条目（案件材料库 200+ 字）
# 尚能到 0.43-0.61，是唯一没暴露该问题的场景。
# 0.12 取自实测短条目下沿 0.13 再留一档余量，宁可少召回也不放噪声进来。
AUTO_RECALL_MIN_SCORE_REAL = float(os.environ.get("AUTO_RECALL_MIN_SCORE_REAL", "0.3"))
AUTO_RECALL_MIN_SCORE_HASH = float(os.environ.get("AUTO_RECALL_MIN_SCORE_HASH", "0.12"))
# 显式覆盖（调试/压测用）：设了就无视后端，一律用它
AUTO_RECALL_MIN_SCORE_OVERRIDE = os.environ.get("AUTO_RECALL_MIN_SCORE")


def auto_recall_min_score(hash_embedding: bool) -> float:
    """返回当前向量化后端下应当生效的合格线。

    :param hash_embedding: True = 当前走离线哈希兜底向量（分数偏低），False = bge-m3
    """
    if AUTO_RECALL_MIN_SCORE_OVERRIDE is not None:
        return float(AUTO_RECALL_MIN_SCORE_OVERRIDE)
    return AUTO_RECALL_MIN_SCORE_HASH if hash_embedding else AUTO_RECALL_MIN_SCORE_REAL

# 四象限图中线（法律轴 / 业务轴各自的中线）
# 语义上不同于上面的「总分档位线」，两者独立命名，不得合并。
# 取值与 SCORE_THRESHOLD_GO 对齐：幂平均恒有 min(x,y) <= M_p(x,y) <= max(x,y)，
# 故两轴均 >= 78 时总分必 >= 78，可杜绝「四象限说强推起诉、决策卡片说补短板」的同屏矛盾。
QUADRANT_AXIS_MID = 78

# ── 回款能力（企查查规则表）的基准与档位 ──────────────────────────
# 为什么基准不是 50：回款能力以幂平均 p=-0.5 参与业务预期，短板主导——
# 基准给 50 等于给每个「公开记录干净」的被告都套了一块短板，判赔分再高也拉不动
# 业务预期。这是把「未查到不利记录」当成了「回款前景中等」，而它实际含义是
# 「没有下调依据」。基准改与决策 GO 线对齐，语义为：
#   公开记录查不到问题 → 按「可取回」处理；有不利往下扣，没有有利不加不减。
RECOVERY_BASE_SCORE = 78.0     # 与 SCORE_THRESHOLD_GO 对齐

# 回款能力单独一套档位，不能套决策分的 62/78：
# 用 62/78 卡一个基准 78 的概率，会把「无信号」判成「待补强」，
# 「无信号 = 偏弱」的矛盾只是换个位置重新出现。
RECOVERY_TIER_OK = 70.0        # >= 70 回款较有保障
RECOVERY_TIER_WEAK = 45.0      # 45-70 中性；< 45 回款困难

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
#
# 这三个是「最后兜底的默认值」，不是写死的供应商。换供应商的正确做法是在
# providers.py 加一条预设（或把 LLM_PROVIDER 指向已有预设），而不是改这里。
# 解析链是：.env / 环境变量显式值 > 预设值 > 本文件的模块常量。
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

# 生成长度上限的字段名。OpenAI / DeepSeek 用 max_tokens；Kimi 已将 max_tokens
# 标为弃用，要求改用 max_completion_tokens。
# 填错的后果是静默的：Kimi 会忽略该字段并回落到默认 131072，而它的限流按这个
# 值预扣额度，低额度账号会在毫无征兆的情况下 429。
LLM_MAX_TOKENS_PARAM = "max_tokens"

_RUNTIME_SETTING_KEYS = [
    "USE_MOCK",
    "LLM_PROVIDER",
    # 供应商中立的 LLM 配置。LLM_* 优先于 DEEPSEEK_*，后者保留只为兼容已有 .env
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_STRONG_MODEL",
    "LLM_FAST_MODEL",
    "LLM_JSON_MODE_MODELS",
    "LLM_MAX_TOKENS_PARAM",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "QCC_API_TOKEN",
    "PKULAW_API_TOKEN",
    "SILICONFLOW_API_KEY",
    # 各家供应商的密钥槽（由 providers.py 推导），新增供应商时不必回来改这里
    *ALL_KEY_ENV_NAMES,
]
_DEFAULTS = {
    "USE_MOCK": "True",
    "LLM_PROVIDER": DEFAULT_PROVIDER_ID,
    "LLM_API_KEY": "",
    "LLM_BASE_URL": "",
    "LLM_STRONG_MODEL": "",
    "LLM_FAST_MODEL": "",
    "LLM_JSON_MODE_MODELS": "",
    "LLM_MAX_TOKENS_PARAM": "",
    "DEEPSEEK_API_KEY": "",
    # 必须留空。它一旦有默认值，就永远优先于预设的 base_url，
    # 切到 Kimi 时地址还指着 DeepSeek——切了个寂寞。
    "DEEPSEEK_BASE_URL": "",
    "QCC_API_TOKEN": "",
    "PKULAW_API_TOKEN": "",
    "SILICONFLOW_API_KEY": "",
    **{name: "" for name in ALL_KEY_ENV_NAMES},
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


def _merged_raw_values() -> dict:
    """
    .env 与环境变量合并后的原始字典（未解析、未脱敏，**含明文密钥**）。

    只在后端内部使用，用于按预设逐个探测「这家有没有配 key」。
    严禁出现在任何 HTTP 响应、日志或前端载荷里。
    """
    file_values = _read_env_file()
    merged = dict(_DEFAULTS)
    # 优先级：默认值 < .env 文件（非空） < 真实环境变量（非空）。
    # 两点容易踩：
    # 1) 空值不参与覆盖。否则 .env 里留空的 LLM_API_KEY= 会把真实环境变量顶掉，
    #    照着 .env.example 复制一份、只填了部分 key 的用户必然中招。
    # 2) 真实环境变量优先于 .env，与主流 dotenv 实现一致。原实现是反的，
    #    导致 export 了 key 却因为 .env 里有一行（哪怕为空）而完全不生效。
    for key, value in file_values.items():
        if key in _RUNTIME_SETTING_KEYS and value.strip():
            merged[key] = value
    for key in _RUNTIME_SETTING_KEYS:
        env_value = os.getenv(key)
        if env_value and env_value.strip():
            merged[key] = env_value
    return merged


def get_runtime_settings() -> dict:
    merged = _merged_raw_values()

    # 解析链：显式值（.env / 环境变量） > 供应商预设 > 模块常量。
    # 显式优先是为了保留手改 .env 的自由度；预设次之，让「选一个预设」就能
    # 拿到成套参数；模块常量只是最后的兜底。
    #
    # 密钥不在这条链上——它只从 providers.resolve_api_key 取，那是全项目
    # 唯一的取凭证入口（接 KMS 时只改那一处）。
    preset = get_preset(merged["LLM_PROVIDER"])
    api_key, key_source = resolve_api_key(preset, merged)
    base_url = (merged["LLM_BASE_URL"].strip()
                or merged["DEEPSEEK_BASE_URL"].strip()
                or preset.base_url
                or "https://api.deepseek.com")
    strong_model = (merged["LLM_STRONG_MODEL"].strip()
                    or preset.strong_model or LLM_STRONG_MODEL)
    fast_model = (merged["LLM_FAST_MODEL"].strip()
                  or preset.fast_model or LLM_FAST_MODEL)
    json_mode_raw = merged["LLM_JSON_MODE_MODELS"].strip()
    json_mode_models = ({m.strip() for m in json_mode_raw.split(",") if m.strip()}
                        if json_mode_raw
                        else (set(preset.json_mode_models) or set(JSON_MODE_MODELS)))

    settings = {
        "use_mock": str(merged["USE_MOCK"]).lower() == "true",
        "llm_provider": preset.id,
        "llm_provider_label": preset.label,
        "llm_key_source": key_source,
        "llm_cost_tier": preset.cost_tier,
        "llm_api_key": api_key,
        "llm_base_url": base_url,
        "llm_strong_model": strong_model,
        "llm_fast_model": fast_model,
        "llm_json_mode_models": json_mode_models,
        "llm_max_tokens_param": (merged["LLM_MAX_TOKENS_PARAM"].strip()
                                 or LLM_MAX_TOKENS_PARAM),
        # 废弃别名：deepseek_* 与 llm_* 指向同一份值。保留只为兼容既有脚本，
        # 新代码一律用 llm_*，不要再新增对别名的依赖。
        "deepseek_api_key": api_key,
        "deepseek_base_url": base_url,
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
