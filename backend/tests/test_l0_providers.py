"""
L0 供应商预设 / 凭证 / 选择三层

这一层的风险不在功能对不对，而在**密钥会不会漏出去、以及切换会不会静默失效**：
产品最终是 SaaS，密钥是我们的成本与责任；而「点了切换没反应」这类失效
在演示现场是最难救的——页面不报错，只是评估还在往旧地址发请求。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import config, providers, settings_store
from core.providers import PRESETS, get_preset, mask_key, resolve_api_key
from core.settings_store import SwitchError

BASE_ENV = """\
# Soft IP 配置
# --- 方案 A：DeepSeek ---
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-deepseek-plain-9527
LLM_BASE_URL=https://api.deepseek.com
QCC_API_TOKEN=qcc-keep-me
"""


@pytest.fixture
def env_file(monkeypatch):
    """
    把 .env 与全部相关环境变量接管到临时目录。

    用 tempfile.mkdtemp 而不是 pytest 的 tmp_path：后者在本机沙箱下建
    pytest-of-unknown 目录会 PermissionError，与被测逻辑毫无关系。
    """
    workdir = Path(tempfile.mkdtemp(prefix="soft-ip-env-"))
    path = workdir / ".env"
    path.write_text(BASE_ENV, encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", path)

    for key in config._RUNTIME_SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield path


@pytest.fixture(autouse=True)
def _no_kms(monkeypatch):
    """每个用例都从「没有 KMS」的干净状态开始，避免互相污染"""
    monkeypatch.setattr(providers, "_secret_resolver", None)


# ============================================================
# A. 预设注册表
# ============================================================

class TestPresetRegistry:

    def test_every_builtin_preset_is_complete(self):
        """缺任何一项，切过去就是半套配置——最容易漏的是 json 白名单"""
        for pid, preset in PRESETS.items():
            assert preset.base_url, f"{pid} 缺 base_url"
            assert preset.strong_model and preset.fast_model, f"{pid} 缺模型名"
            assert preset.json_mode_models, f"{pid} 缺 json 模式白名单"
            assert preset.key_envs, f"{pid} 缺密钥环境变量名"
            assert preset.max_tokens_param

    def test_presets_never_carry_a_secret(self):
        """
        预设是可进版本库的公开数据，绝不能藏 key。

        这条不是为了防自己写错，是为了防「以后有人图省事把测试用的 key
        贴在预设里」。字段值里出现 sk- 前缀就拦下来。
        """
        for pid, preset in PRESETS.items():
            for field in ("base_url", "strong_model", "fast_model",
                          "max_tokens_param", "console_url", "notes"):
                value = getattr(preset, field) or ""
                assert "sk-" not in value, f"{pid}.{field} 疑似含密钥"

    def test_adding_a_provider_needs_no_config_edit(self):
        """
        「加一家只改一处」：预设里声明的密钥变量名必须自动进入配置的合并范围。

        否则新增供应商时还得回头改 config._RUNTIME_SETTING_KEYS——忘了就是
        配了 key 却读不到，而报错只是「未配置 LLM_API_KEY」，完全指不到真因。
        """
        declared = set(providers.ALL_KEY_ENV_NAMES)
        for pid, preset in PRESETS.items():
            assert set(preset.key_envs) <= declared, f"{pid} 的密钥槽未纳入合并范围"
            for name in preset.key_envs:
                assert name in config._RUNTIME_SETTING_KEYS, f"{name} 未纳入配置合并"

    def test_unknown_id_falls_back_to_custom(self):
        """手改 .env 写了个不存在的 id，不该让配置解析整个崩掉"""
        preset = get_preset("no-such-vendor")
        assert preset.id == providers.CUSTOM_PROVIDER_ID
        assert preset.base_url == ""       # 自定义不提供默认值，交给显式配置

    def test_default_provider_is_registered(self):
        assert providers.DEFAULT_PROVIDER_ID in PRESETS


# ============================================================
# B. 凭证取值
# ============================================================

class TestKeyResolution:

    def test_provider_specific_slot_wins(self):
        """两家 key 都配着时，各家认各家的——这是来回切换能成立的前提"""
        key, source = resolve_api_key(PRESETS["kimi"], {
            "KIMI_API_KEY": "sk-kimi-1111", "LLM_API_KEY": "sk-deepseek-2222"})
        assert key == "sk-kimi-1111"
        assert source == "KIMI_API_KEY"

    def test_neutral_slot_is_the_fallback(self):
        """只有一把通用 key 时，任何供应商都该能跑起来"""
        key, source = resolve_api_key(PRESETS["kimi"], {"LLM_API_KEY": "sk-2222"})
        assert (key, source) == ("sk-2222", "LLM_API_KEY")

    def test_deepseek_legacy_slot_still_works(self):
        key, source = resolve_api_key(PRESETS["deepseek"], {"DEEPSEEK_API_KEY": "sk-old"})
        assert (key, source) == ("sk-old", "DEEPSEEK_API_KEY")

    def test_no_key_at_all(self):
        assert resolve_api_key(PRESETS["kimi"], {}) == ("", "")

    def test_kms_resolver_takes_over(self, monkeypatch):
        """SaaS 化时接 KMS 的接缝：注入解析器后不再读环境变量"""
        monkeypatch.setattr(providers, "_secret_resolver",
                            lambda name: "from-kms" if name == "KIMI_API_KEY" else None)
        key, source = resolve_api_key(PRESETS["kimi"], {"KIMI_API_KEY": "from-env"})
        assert key == "from-kms"

    def test_cross_provider_fallback_warns(self):
        """
        密钥不是从该供应商自己的槽里拿的，必须告警。

        这是换供应商后最贵的一种失败：请求发出去、限流额度扣了，回来 401，
        而日志上只写着 Invalid API key——谁都看不出是 key 拿错了。
        """
        warn = providers.cross_provider_fallback_warning(PRESETS["kimi"], "LLM_API_KEY")
        assert "401" in warn and "KIMI_API_KEY" in warn
        assert providers.cross_provider_fallback_warning(PRESETS["kimi"], "KIMI_API_KEY") == ""

    def test_mask_keeps_only_the_last_four(self):
        """
        掩码不能露出厂商前缀。

        前缀（sk-、sk-or-v1-）虽然不算密钥本体，但它是可被用于定向撞库的
        元信息；确认「是不是我填的那把」用末 4 位就够了。
        """
        masked = mask_key("sk-deepseek-plain-9527")
        assert masked == "••••9527"
        assert "sk-" not in masked and "deepseek" not in masked

    def test_short_keys_are_fully_masked(self):
        assert mask_key("ab") == "••••"
        assert mask_key("") == ""


# ============================================================
# C. 切换（选择层）
# ============================================================

class TestProviderSwitch:

    def test_switch_writes_the_whole_group(self, env_file, monkeypatch):
        """
        切换必须整组改，不能只改 LLM_PROVIDER。

        只改 id 的话，.env 里还留着上一家的 base_url 和模型名，
        请求照旧发往旧地址——界面显示已切换到 Kimi，实际还在调 DeepSeek。
        """
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")

        text = env_file.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=kimi" in text
        assert "LLM_BASE_URL=https://api.moonshot.ai/v1" in text
        assert "LLM_FAST_MODEL=kimi-k2.6" in text
        assert "LLM_JSON_MODE_MODELS=kimi-k2.6" in text
        assert "LLM_MAX_TOKENS_PARAM=max_completion_tokens" in text

    def test_switch_takes_effect_immediately(self, env_file, monkeypatch):
        """配置每次调用重读，切完就得生效——不该要求重启服务"""
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")

        s = config.get_runtime_settings()
        assert s["llm_provider"] == "kimi"
        assert s["llm_base_url"] == "https://api.moonshot.ai/v1"
        assert s["llm_fast_model"] == "kimi-k2.6"
        assert s["llm_max_tokens_param"] == "max_completion_tokens"

    def test_switch_never_touches_keys(self, env_file, monkeypatch):
        """
        切换只改地址和模型名，绝不写任何密钥行。

        .env 里的密钥是明文，多写一行就多一处泄露面；而且切到 Kimi 时
        把 DeepSeek 的 key 写进 KIMI_API_KEY 是彻底的错配。
        """
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")

        text = env_file.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-deepseek-plain-9527" in text   # 原样保留
        assert "KIMI_API_KEY=" not in text                    # 不代写
        assert "sk-kimi-3333" not in text                     # 不落盘

    def test_switch_preserves_comments_and_unrelated_keys(self, env_file, monkeypatch):
        """
        逐行替换而不是整份重写。

        整份重写会把 .env.example 里那些「# --- 方案 B：Kimi ---」说明清光，
        下次打开只剩一堆光秃秃的键值，照着改的人无从下手。
        """
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")

        text = env_file.read_text(encoding="utf-8")
        assert "# --- 方案 A：DeepSeek ---" in text
        assert "QCC_API_TOKEN=qcc-keep-me" in text

    def test_commented_out_keys_are_left_alone(self, env_file, monkeypatch):
        """注释掉的备用配置块不能被误当成现役配置改掉"""
        env_file.write_text(BASE_ENV + "# LLM_BASE_URL=https://api.openai.com/v1\n",
                            encoding="utf-8")
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")

        assert "# LLM_BASE_URL=https://api.openai.com/v1" in env_file.read_text(encoding="utf-8")

    def test_switch_to_unconfigured_provider_is_rejected(self, env_file):
        """
        没配密钥就不许切过去。

        允许切的话，用户会在评估跑到一半时收到 401——比在切换的那一刻
        就拦下来难排查得多。
        """
        with pytest.raises(SwitchError) as exc:
            settings_store.select_provider("kimi")
        assert "KIMI_API_KEY" in str(exc.value)

    def test_generic_slot_key_does_not_unlock_another_vendor(self, env_file):
        """
        关键闸门：只有通用槽里那把 DeepSeek 的 key，不允许切到 Kimi。

        运行时取值允许回落到通用槽（只有一把 key 的人任何供应商都能跑），
        但「切换」这个动作不行——否则用户以为是切过去了，实际每次调用都
        拿 DeepSeek 的 key 去敲 Kimi 的门，401 要等评估跑起来才炸。
        """
        # .env 里有 LLM_API_KEY，Kimi 的专属槽却是空的
        assert config.get_runtime_settings()["llm_api_key"] == "sk-deepseek-plain-9527"
        with pytest.raises(SwitchError, match="专属密钥"):
            settings_store.select_provider("kimi")
        assert env_file.read_text(encoding="utf-8") == BASE_ENV   # 什么都没被写坏

    def test_switch_succeeds_once_own_slot_is_configured(self, env_file, monkeypatch):
        """配好专属槽后就能切，且运行时解析到的是专属槽而不是通用槽"""
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        settings_store.select_provider("kimi")
        s = config.get_runtime_settings()
        assert s["llm_key_source"] == "KIMI_API_KEY"
        assert s["llm_api_key"] == "sk-kimi-3333"

    def test_switch_rejects_unknown_id(self, env_file):
        with pytest.raises(SwitchError, match="未知供应商"):
            settings_store.select_provider("no-such-vendor")

    def test_env_var_overrides_are_reported(self, env_file, monkeypatch):
        """
        环境变量优先级高于 .env，被顶住时必须说出来。

        否则用户点了保存、界面也提示成功，可请求还是走旧地址——
        「设置页改不动」的锅会被算到产品头上。
        """
        monkeypatch.setenv("LLM_BASE_URL", "https://pinned.example.com")
        assert "LLM_BASE_URL" in settings_store.env_overrides()
        assert any("环境变量" in w for w in settings_store.snapshot()["warnings"])


# ============================================================
# D. HTTP 接口：密钥不得外泄
# ============================================================

class TestSettingsApi:
    """
    接口层的底线：不收密钥、不吐密钥。

    产品以 SaaS 形态交付，调用用的是我们的 key。前端既看不到也填不了，
    这条边界一旦破掉，泄露面就从「服务端」扩散到「每个登录用户的浏览器」。
    """

    @pytest.fixture
    def client(self, env_file, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app) as c:
            yield c

    def test_no_plaintext_key_in_response(self, client):
        body = client.get("/api/settings/providers").text
        assert "sk-deepseek-plain-9527" not in body
        assert "plain-9527" not in body          # 连末 8 位都不该出现

    def test_key_is_masked(self, client):
        snap = client.get("/api/settings/providers").json()
        assert snap["key_masked"] == "••••9527"
        for item in snap["providers"]:
            if item["available"]:
                assert item["key_masked"].startswith("••••")

    def test_post_rejects_api_key_field(self, client, monkeypatch):
        """
        切换只认 provider_id。

        extra=forbid 是刻意的护栏：万一以后有人「顺手」加了 api_key 字段，
        请求直接 400，而不是悄悄把密钥写进 .env。
        """
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-3333")
        resp = client.post("/api/settings/provider",
                           json={"provider_id": "kimi", "api_key": "sk-evil"})
        assert resp.status_code == 422
        assert "extra_forbidden" in resp.text or "api_key" in resp.text

    def test_post_unknown_provider_is_400(self, client):
        resp = client.post("/api/settings/provider", json={"provider_id": "nope"})
        assert resp.status_code == 400
        assert "未知供应商" in resp.text

    def test_post_unconfigured_provider_is_400(self, client):
        resp = client.post("/api/settings/provider", json={"provider_id": "kimi"})
        assert resp.status_code == 400
        assert "KIMI_API_KEY" in resp.text

    def test_ping_is_skipped_in_mock_mode(self, client):
        """
        Mock 模式下不发起真实调用。

        自检的意义是验证 key 与地址，mock 下两者都不参与，跑一次只会得到
        「成功」这个假结论。
        """
        resp = client.post("/api/settings/providers/deepseek/test").json()
        assert resp["ok"] is False
        assert "Mock" in resp.get("skipped", "")


# ============================================================
# E. 页脚运行模式接口（/settings/mode）
# ============================================================

class TestRunModeApi:
    """
    页脚那一行「真实模式 / Mock 模式」的数据源。

    这个接口是**刻意跟 /providers 分开**的：页脚只需要回答「屏幕上这些结论是
    真模型跑出来的还是演示数据」，没必要为此把供应商清单和密钥掩码也拉进
    每一次页面加载。所以它有一条自己的底线——连掩码都不返回。
    """

    @pytest.fixture
    def client(self, env_file, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app) as c:
            yield c

    def test_reports_mock_mode(self, client):
        body = client.get("/api/settings/mode").json()
        assert body["mock"] is True
        assert body["provider_id"] == "deepseek"

    def test_never_returns_any_key_material(self, client):
        """
        连掩码都不给。

        「只有末 4 位」这个理由在 /providers 上成立（运维需要确认是不是自己填的那把），
        但页脚没有这个需求，多回一个字段就多一条泄露路径。
        """
        raw = client.get("/api/settings/mode").text
        assert "sk-deepseek-plain-9527" not in raw
        assert "9527" not in raw                      # 末 4 位也不该出现
        assert "••••" not in raw
        assert "key_masked" not in raw

    def test_reports_models_and_provider(self, client):
        body = client.get("/api/settings/mode").json()
        assert body["provider_label"] == "DeepSeek"
        assert body["strong_model"] == "deepseek-reasoner"
        assert body["fast_model"] == "deepseek-chat"
        assert body["base_url"] == "https://api.deepseek.com"

    def test_real_mode_without_key_is_flagged(self, client, env_file, monkeypatch):
        """
        真实模式却没密钥 —— 一调就炸。

        这是页脚唯一需要报警的组合：它必须显示「未配置密钥，调用会失败」，
        而不是安安静静地写一句「真实模式」，让人以为一切正常。
        """
        monkeypatch.setenv("USE_MOCK", "False")
        env_file.write_text(
            "LLM_PROVIDER=deepseek\nLLM_BASE_URL=https://api.deepseek.com\n",
            encoding="utf-8",
        )
        body = client.get("/api/settings/mode").json()
        assert body["mock"] is False
        assert body["key_configured"] is False

    def test_real_mode_with_key_is_healthy(self, client, env_file, monkeypatch):
        monkeypatch.setenv("USE_MOCK", "False")
        body = client.get("/api/settings/mode").json()
        assert body["mock"] is False
        assert body["key_configured"] is True


# ============================================================
# F. 测试环境自身的隔离（护栏）
# ============================================================

class TestEnvIsolation:
    """
    守住 conftest 里那条 .env 隔离夹具。

    它挂了不会有任何测试立刻变红——只是红灯会在几个月后以「TestProviderSwap
    三个用例莫名其妙失败」的形式回来，而那时没人会想到是 conftest 被改过。
    所以这里显式钉住：测试期间读的一定是临时 .env，不是仓库里那份真的。
    """

    def test_tests_never_read_the_project_dotenv(self):
        assert config.ENV_PATH != config.PROJECT_DIR / ".env"
        assert config.ENV_PATH.parent != config.PROJECT_DIR

    def test_isolated_dotenv_is_outside_the_repo(self):
        """隔离路径必须落在临时目录里，绝不能碰仓库内任何文件。"""
        assert str(config.ENV_PATH).startswith(str(Path(tempfile.gettempdir())))

    def test_switching_provider_writes_only_the_isolated_file(self, monkeypatch):
        """
        最危险的一种回归：切换供应商会把整组参数写进 config.ENV_PATH，
        隔离一旦失效，写的是开发者本地那份真 .env——不报错，直接改人家的配置。
        """
        real_env = config.PROJECT_DIR / ".env"
        read_real = lambda: (real_env.read_text(encoding="utf-8")
                             if real_env.exists() else None)
        before = read_real()

        # 隔离的 .env 是空的，未配专属密钥的供应商不允许切换（那条闸门本身是对的）
        monkeypatch.setenv("KIMI_API_KEY", "sk-isolation-guard-1234")
        try:
            settings_store.select_provider("kimi")
            after = read_real()
        finally:
            # 本用例的失败路径恰恰意味着「真实 .env 已被改写」。
            # 只断言不修复的话，跑一次测试就把开发者的配置改了，还留一地鸡毛——
            # 断言负责报警，这里负责把现场恢复原样。
            if read_real() != before and before is not None:
                real_env.write_text(before, encoding="utf-8")

        assert after == before, "切换供应商改动了仓库里的真实 .env"
        assert "kimi" in config.ENV_PATH.read_text(encoding="utf-8"), \
            "切换结果应落在隔离的临时 .env 里"
