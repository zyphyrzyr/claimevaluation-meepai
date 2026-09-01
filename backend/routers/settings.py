"""
运行设置路由 —— LLM 供应商切换（高级设置）

安全模型（SaaS 形态下这一条是底线）：
    本文件的接口**只接受 provider_id，不接受任何密钥**，也**只回显掩码**。
    密钥属于部署方，由环境变量 / KMS 提供；用户付费买的是调用，不是把 key
    交给他。前端因此既看不到、也填不了 key——看得见就意味着能泄露。

    POST /provider 用 extra="forbid" 把多余字段挡在门外：万一以后有人
    「顺手」加个 api_key 字段，请求会直接 400，而不是悄悄把密钥写进 .env。
"""
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, HTTPException

from core import config, llm_gateway, settings_store
from core.providers import PRESETS
from core.settings_store import SwitchError

router = APIRouter()


@router.get("/providers")
def list_providers():
    """可选供应商 + 当前生效配置（密钥仅掩码）"""
    return settings_store.snapshot()


class ProviderSelect(BaseModel):
    # forbid 是刻意的：切换只认 provider_id。传 api_key 之类的字段直接 400，
    # 免得哪天被人顺手加上「顺手把密钥写进 .env」的能力。
    model_config = ConfigDict(extra="forbid")

    provider_id: str


@router.post("/provider")
def select_provider(payload: ProviderSelect):
    try:
        result = settings_store.select_provider(payload.provider_id)
    except SwitchError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"写入 {config.ENV_PATH} 失败：{e}")
    return result


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: str):
    """
    连通性自检。

    Mock 模式下不发起真实调用——自检的意义是验证 key 和地址，mock 下两者
    都不参与，跑一次只会得到「成功」这个假结论，反而更危险。
    """
    if (provider_id or "").strip() not in PRESETS:
        raise HTTPException(404, f"未知供应商 {provider_id!r}")

    snapshot = settings_store.snapshot()
    if snapshot["mock"]:
        return {"ok": False, "skipped":
                "当前为 Mock 模式，未发起真实调用（自检只在真实模式下有意义）"}

    current = snapshot["current"]["id"]
    if current != provider_id:
        return {"ok": False, "skipped":
                f"该供应商未生效：当前使用的是 {current}。先切换再自检，"
                f"否则测的是当前供应商而不是选中的这个。"}

    return llm_gateway.ping()
