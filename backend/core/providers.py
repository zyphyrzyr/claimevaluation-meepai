"""
LLM 供应商预设注册表

三层分离模型（本节目的：让「换供应商」和「管密钥」彻底解耦）：

  预设（本文件）
      技术参数：base_url / 强模型 / 普通模型 / json 模式白名单 / 长度字段名 / 成本档位。
      可公开、可进版本库。**不含任何密钥**。
  凭证（环境变量或 KMS）
      各家自己的 *_API_KEY。不进版本库、不进数据库、不写 .env 之外的任何地方，
      也不回显给前端——接口只给掩码。
  选择（.env 的 LLM_PROVIDER）
      当前用哪个预设，由设置页写入。改完即刻生效（配置每次调用重读，不缓存）。

新增一个供应商 = 在 PRESETS 里加一条，其余代码零改动。这是本文件唯一的设计目标。

本模块不 import config（config 会 import 本模块），只做纯数据与纯函数。
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

# 成本档位。阶段一只登记、不过滤：演示期所有预设都可选。
# SaaS 化后由套餐决定可选范围——Kimi K3 与 K2.6 价差 3-4 倍，
# 放开自由选等于把成本敞口交给用户。
COST_TIERS: Dict[str, Dict[str, str]] = {
    "low": {"label": "低成本"},
    "medium": {"label": "中成本"},
    "high": {"label": "高成本"},
}

# 套餐 → 可选成本档位。plan 为 None 时不做任何过滤（当前行为）。
PLAN_COST_TIERS: Dict[str, Tuple[str, ...]] = {
    "free": ("low",),
    "pro": ("low", "medium"),
    "enterprise": ("low", "medium", "high"),
}


@dataclass(frozen=True)
class ProviderPreset:
    """一个供应商的技术参数。注意：字段里永远不出现 key。"""
    id: str
    label: str
    base_url: str
    strong_model: str
    fast_model: str
    json_mode_models: Tuple[str, ...]
    key_envs: Tuple[str, ...]              # 按优先级排列的取 key 环境变量名
    max_tokens_param: str = "max_tokens"
    cost_tier: str = "medium"
    console_url: str = ""
    notes: str = ""
    builtin: bool = True

    @property
    def primary_key_env(self) -> str:
        return self.key_envs[0] if self.key_envs else ""


PRESETS: Dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        strong_model="deepseek-reasoner",
        fast_model="deepseek-chat",
        json_mode_models=("deepseek-chat",),
        # 默认供应商的 key 槽就是通用槽 LLM_API_KEY，历史 DEEPSEEK_API_KEY 仍兼容，
        # 这样已有 .env 一字不改也能跑。
        key_envs=("LLM_API_KEY", "DEEPSEEK_API_KEY"),
        max_tokens_param="max_tokens",
        cost_tier="low",
        console_url="https://platform.deepseek.com/api_keys",
        notes="deepseek-reasoner 不接受 response_format=json_object（连同 temperature、"
              "function calling 一并忽略），下发该参数会直接 400，故 json 白名单只列 "
              "deepseek-chat。",
    ),
    "kimi": ProviderPreset(
        id="kimi",
        label="Kimi（Moonshot）",
        base_url="https://api.moonshot.ai/v1",
        strong_model="kimi-k3",
        fast_model="kimi-k2.6",
        json_mode_models=("kimi-k2.6",),
        key_envs=("KIMI_API_KEY", "LLM_API_KEY"),
        max_tokens_param="max_completion_tokens",
        cost_tier="high",
        console_url="https://platform.moonshot.ai/console/api-keys",
        notes="两个坑：① Kimi 已把 max_tokens 标为弃用，必须用 max_completion_tokens。"
              "填错不报错，只会回落到默认 131072，而它的限流按这个值预扣额度，"
              "低额度账号会莫名 429。② moonshot-v1 全系列与 kimi-k2.5 已于 "
              "2026-08-31 下线，填这些名字只会拿到 404。",
    ),
    "openai": ProviderPreset(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        strong_model="gpt-4o",
        fast_model="gpt-4o-mini",
        json_mode_models=("gpt-4o", "gpt-4o-mini"),
        key_envs=("OPENAI_API_KEY", "LLM_API_KEY"),
        max_tokens_param="max_tokens",
        cost_tier="medium",
        console_url="https://platform.openai.com/api-keys",
        notes="模型名请按 OpenAI 官方当前可用列表核对后再用于生产。",
    ),
}

DEFAULT_PROVIDER_ID = "deepseek"

# 自定义：预设字段全空，等价于「只认 .env 里手写的 LLM_*，回落模块常量」。
# 有人手改 .env 写了个不在注册表里的 id 时用它兜底，不至于让整个配置解析崩掉。
CUSTOM_PROVIDER_ID = "custom"
CUSTOM_PRESET = ProviderPreset(
    id=CUSTOM_PROVIDER_ID,
    label="自定义（手写 .env）",
    base_url="",
    strong_model="",
    fast_model="",
    json_mode_models=(),
    key_envs=("LLM_API_KEY",),
    builtin=False,
    notes="不在预设注册表内：base_url、模型名等全部取自 .env 手写的 LLM_* 项。",
)

# 所有可能存放密钥的环境变量名。config 需要把它们纳入合并范围，
# 否则预设里新增一家供应商，还得回头改 config——那就不叫「只改一处」了。
ALL_KEY_ENV_NAMES = sorted({name for p in PRESETS.values()
                            for name in p.key_envs} | {name for name in CUSTOM_PRESET.key_envs})


def get_preset(provider_id: Optional[str]) -> ProviderPreset:
    """按 id 取预设。空 / 未知 id 一律回落到自定义预设，不让配置解析崩掉。"""
    if not provider_id:
        return PRESETS[DEFAULT_PROVIDER_ID]
    return PRESETS.get(provider_id.strip(), CUSTOM_PRESET)


def list_presets(plan: Optional[str] = None) -> List[ProviderPreset]:
    """
    可选择的预设列表。

    plan 为 None 时返回全部（阶段一的演示期行为）。SaaS 化后传套餐名即可按
    cost_tier 过滤——钩子先备好，避免到时候改数据结构导致前端已渲染的选项突然消失。
    """
    presets = list(PRESETS.values())
    if plan is None:
        return presets
    allowed = PLAN_COST_TIERS.get(plan)
    if allowed is None:
        return presets
    return [p for p in presets if p.cost_tier in allowed]


# ---------------------------------------------------------- 凭证

# KMS / Vault 的接缝。阶段一是 None，取值走环境变量；SaaS 化时注入一个
# fn(env_name) -> Optional[str] 即可，调用方无需感知。
_secret_resolver: Optional[Callable[[str], Optional[str]]] = None


def set_secret_resolver(fn: Optional[Callable[[str], Optional[str]]]) -> None:
    """注入密钥解析器（接 KMS 时调用）。传 None 恢复为环境变量模式。"""
    global _secret_resolver
    _secret_resolver = fn


def resolve_api_key(preset: ProviderPreset,
                    values: Dict[str, str]) -> Tuple[str, str]:
    """
    取某个供应商的密钥，返回 (密钥, 来源环境变量名)。

    **全项目唯一的取凭证入口。** 阶段一实现为「按预设声明的环境变量名依次查找」，
    KMS 只是预留接缝，不改变调用方式。

    顺序即优先级：预设自己的槽 > 通用槽 LLM_API_KEY > 历史槽。
    各家独立槽优先，是为了让「两家 key 都填着、来回切换」这个用法成立；
    LLM_API_KEY 作为通用槽兜底，则保证只有一把 key 时任何供应商都能跑。
    """
    for name in preset.key_envs:
        if _secret_resolver is not None:
            try:
                got = _secret_resolver(name)
            except Exception:
                got = None
            if got and got.strip():
                return got.strip(), name
        raw = values.get(name)
        if raw and raw.strip():
            return raw.strip(), name
    return "", ""


def mask_key(key: str) -> str:
    """
    密钥掩码：只保留末 4 位。

    前几位不能露——厂商前缀（sk-、sk-or-v1- 之类）本身不敏感，但它是可被
    用来做定向撞库的元信息，没必要给。末 4 位足够让人确认「是不是我填的那把」。
    """
    if not key:
        return ""
    if len(key) <= 4:
        return "••••"
    return f"••••{key[-4:]}"


def cross_provider_fallback_warning(preset: ProviderPreset, key_source: str) -> str:
    """
    密钥不是从该供应商自己的槽里取到时给出告警。

    这是换供应商后最贵的一种失败：请求发出去了、钱扣了限流额度，回来的却是
    401，而日志上只写着 Invalid API key——谁都看不出是 key 拿错了。
    """
    if not key_source or not preset.key_envs:
        return ""
    if key_source == preset.key_envs[0]:
        return ""
    return (f"当前密钥来自通用槽 {key_source}，不是 {preset.label} 专属的 "
            f"{preset.key_envs[0]}。若这把 key 属于其他厂商，调用会返回 401。")


# ---------------------------------------------------------- 序列化

def preset_to_dict(preset: ProviderPreset, *, key: str = "",
                   key_source: str = "") -> Dict[str, object]:
    """
    预设 → 可下发的字典。

    key 由调用方解析后传入，这里只做掩码；**不要**把原始 key 塞进来。
    """
    return {
        "id": preset.id,
        "label": preset.label,
        "base_url": preset.base_url,
        "strong_model": preset.strong_model,
        "fast_model": preset.fast_model,
        "json_mode_models": list(preset.json_mode_models),
        "max_tokens_param": preset.max_tokens_param,
        "cost_tier": preset.cost_tier,
        "cost_tier_label": COST_TIERS.get(preset.cost_tier, {}).get("label", preset.cost_tier),
        "console_url": preset.console_url,
        "notes": preset.notes,
        "builtin": preset.builtin,
        "primary_key_env": preset.primary_key_env,
        "available": bool(key),
        "key_source": key_source or None,
        "key_masked": mask_key(key) or None,
    }
