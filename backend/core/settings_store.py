"""
供应商选择的读写（三层模型里的「选择层」）

预设在 providers.py（代码），凭证在环境变量，这里只管「当前选了谁」——
一条 LLM_PROVIDER，外加切换时整组写入的 LLM_* 派生参数。

两条硬约束：

1. 本模块**绝不写任何 *_API_KEY**。密钥只能由部署方通过环境变量 / KMS 提供，
   落在 .env 里是明文、会被 .gitignore 之外的操作误提交。切换供应商只改地址
   和模型名，key 各家自己认自己的槽。

2. 写入走逐行替换而不是整份重写，为的是保住 .env 里用户手写的注释、
   分节标题和无关条目。整份重写会把「# --- 方案 B：Kimi ---」这类说明清光，
   下次打开就只剩一堆光秃秃的键值，照着改的人无从下手。
"""
import os
import re
from typing import Dict, List

from . import config
from . import providers
from .providers import PRESETS, ProviderPreset, mask_key

# 切换供应商时整组重写的键。注意这里没有、也永远不该有任何一个 *_API_KEY。
DERIVED_KEYS = (
    "LLM_BASE_URL",
    "LLM_STRONG_MODEL",
    "LLM_FAST_MODEL",
    "LLM_JSON_MODE_MODELS",
    "LLM_MAX_TOKENS_PARAM",
)
SELECTION_KEY = "LLM_PROVIDER"

_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")


class SwitchError(ValueError):
    """切换失败（未知 id、不可写、目标未配密钥等）。消息可直接回显给用户。"""


def _preset_values(preset: ProviderPreset) -> Dict[str, str]:
    return {
        "LLM_BASE_URL": preset.base_url,
        "LLM_STRONG_MODEL": preset.strong_model,
        "LLM_FAST_MODEL": preset.fast_model,
        "LLM_JSON_MODE_MODELS": ",".join(preset.json_mode_models),
        "LLM_MAX_TOKENS_PARAM": preset.max_tokens_param,
    }


def env_overrides() -> List[str]:
    """
    当前被真实环境变量顶住的键。

    环境变量优先级高于 .env，所以这些键存在时，设置页改 .env 是不生效的。
    不报出来，用户就会以为「点了保存没反应」——比直接告诉他差得多。
    """
    watched = (SELECTION_KEY,) + DERIVED_KEYS + tuple(providers.ALL_KEY_ENV_NAMES)
    return [k for k in watched if (os.getenv(k) or "").strip()]


def read_selection() -> Dict[str, object]:
    """当前选中的供应商 id，以及它是从哪来的（环境变量 / .env / 默认）。"""
    if (os.getenv(SELECTION_KEY) or "").strip():
        return {"provider_id": os.getenv(SELECTION_KEY).strip(), "source": "env"}
    file_value = (config._read_env_file().get(SELECTION_KEY) or "").strip()
    if file_value:
        return {"provider_id": file_value, "source": "file"}
    return {"provider_id": providers.DEFAULT_PROVIDER_ID, "source": "default"}


def select_provider(provider_id: str) -> Dict[str, object]:
    """
    切换供应商：把 LLM_PROVIDER 与该预设的整组参数写进 .env。

    不改任何密钥行，也不碰其他条目。
    """
    preset = PRESETS.get((provider_id or "").strip())
    if preset is None:
        known = "、".join(PRESETS)
        raise SwitchError(f"未知供应商 {provider_id!r}；可选：{known}")

    raw = config._merged_raw_values()
    key, source = providers.resolve_api_key(preset, raw)
    own_slot = preset.key_envs[0] if preset.key_envs else ""

    # 闸门比运行时取值更严：必须来自该供应商自己的槽。
    #
    # 运行时允许回落到通用槽 LLM_API_KEY（只有一把 key 的人任何供应商都能跑），
    # 但切换这个动作不行——通用槽里放的是默认供应商的 key，拿它去调 Kimi
    # 只会得到 401，而这个 401 要等评估跑到一半才炸出来。宁可在切换的
    # 这一刻就拦下，让用户先去配好专属密钥。
    if not key or source != own_slot:
        cross = (f"目前只有通用槽 {source} 里的一把 key，它属于别的厂商，"
                 f"拿去调 {preset.label} 只会得到 401。") if key else ""
        raise SwitchError(
            f"{preset.label} 尚未配置专属密钥：请在服务端设置环境变量 {own_slot}"
            f"后重启服务。{cross}为避免评估进行中突然调不通，未配好专属密钥的"
            f"供应商不允许切换。"
        )

    updates = {SELECTION_KEY: preset.id, **_preset_values(preset)}
    written = _write_env(updates)
    return {
        "ok": True,
        "provider_id": preset.id,
        "written_keys": written,
        "env_path": str(config.ENV_PATH),
        "warnings": _warnings(),
    }


def _write_env(updates: Dict[str, str]) -> List[str]:
    """逐行替换，保住注释与无关条目；键不存在则追加到文件末尾。"""
    original = ""
    if config.ENV_PATH.exists():
        original = config.ENV_PATH.read_text(encoding="utf-8")

    lines = original.splitlines()
    seen, out = set(), []
    for line in lines:
        match = _ASSIGNMENT.match(line.strip())
        # 注释行（# LLM_BASE_URL=...）不匹配，因此会被原样保留：
        # .env.example 里那些带说明的备用配置块不能被误改。
        if match and match.group(1) in updates:
            key = match.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    appended = [k for k in updates if k not in seen]
    if appended:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{k}={updates[k]}" for k in appended)

    config.ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    return list(updates)


def _warnings() -> List[str]:
    warnings = []
    overridden = env_overrides()
    if overridden:
        warnings.append(
            "以下环境变量会覆盖设置页的选择（环境变量优先级高于 .env）："
            + "、".join(overridden)
            + "。start.sh 之类的启动脚本 export 过的变量，需要在那里改。"
        )
    return warnings


# ---------------------------------------------------------- 快照

def snapshot() -> Dict[str, object]:
    """设置页要的全部信息。密钥只以掩码形式出现。"""
    effective = config.get_runtime_settings()
    raw = config._merged_raw_values()
    selection = read_selection()
    current_preset = providers.get_preset(selection["provider_id"])

    items = []
    for preset in providers.list_presets():
        key, source = providers.resolve_api_key(preset, raw)
        items.append(providers.preset_to_dict(preset, key=key, key_source=source))

    current_key = effective["llm_api_key"]
    current_item = providers.preset_to_dict(
        current_preset, key=current_key, key_source=effective["llm_key_source"])

    # 生效值可能已被 .env 里手写的 LLM_* 覆盖，所以把这些实际生效的值并到
    # current 上——否则界面显示的是预设值，跑的却是另一套，比不显示更糟。
    current_item.update({
        "effective_base_url": effective["llm_base_url"],
        "effective_strong_model": effective["llm_strong_model"],
        "effective_fast_model": effective["llm_fast_model"],
        "effective_json_mode_models": sorted(effective["llm_json_mode_models"]),
        "effective_max_tokens_param": effective["llm_max_tokens_param"],
    })

    warnings = _warnings()
    cross = providers.cross_provider_fallback_warning(
        current_preset, effective["llm_key_source"])
    if cross:
        warnings.append(cross)

    return {
        "current": current_item,
        "selection_source": selection["source"],
        "providers": items,
        "mock": effective["use_mock"],
        "mock_controlled_by": "env" if (os.getenv("USE_MOCK") or "").strip() else "dotenv",
        "warnings": warnings,
        "key_masked": mask_key(current_key) or None,
        "env_path": str(config.ENV_PATH),
    }
