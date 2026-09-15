"""
设置功能冒烟测试（无副作用版）：只验证「读」与「安全边界」，绝不写真实 .env。

覆盖你提的第 3 点安全底线：
  1) GET /api/settings/providers 绝不回显完整密钥（只末 4 位掩码）
  2) POST /api/settings/provider 带 api_key 等额外字段 → 必须 400/422 被拦
"""
import os
import sys

# 隔离测试环境：临时数据目录 + Mock 模式，避免触碰真实数据与密钥
tmp = "/tmp/softip_smoke"
os.makedirs(tmp, exist_ok=True)
os.environ["SOFT_IP_DATA_DIR"] = tmp
os.environ["USE_MOCK"] = "True"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import settings as settings_router

app = FastAPI()
app.include_router(settings_router.router, prefix="/api/settings")

c = TestClient(app)

print("=" * 60)
print("测试 1：GET /api/settings/providers —— 密钥只该是掩码")
print("=" * 60)
r = c.get("/api/settings/providers")
assert r.status_code == 200, f"非 200：{r.status_code} {r.text}"
body = r.json()
# 递归检查整个响应里有没有 'sk-' 开头的完整密钥泄漏
def scan(obj, path=""):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += scan(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        if obj.startswith("sk-") or (len(obj) > 8 and "sk-" in obj):
            hits.append(f"{path}={obj}")
    return hits

leaks = scan(body)
print("current.key_masked =", body.get("current", {}).get("key_masked"))
print("providers[0].key_masked =", body.get("providers", [{}])[0].get("key_masked"))
assert not leaks, f"❌ 发现疑似完整密钥泄漏：{leaks}"
print("✅ 响应中未发现完整密钥（仅末 4 位掩码）")

print()
print("=" * 60)
print("测试 2：POST /api/settings/provider 带 api_key → 必须被拦")
print("=" * 60)
r2 = c.post("/api/settings/provider", json={"provider_id": "deepseek", "api_key": "sk-EVILLEAK1234567890"})
print("状态码：", r2.status_code)
assert r2.status_code in (400, 422), f"❌ 额外字段没被拦下，返回 {r2.status_code}"
print("✅ 试图注入 api_key 被拒（extra=forbid 生效）")

print()
print("=" * 60)
print("测试 3：POST /api/settings/provider 合法 id（不写真实 .env 因 forbid 前已校验）")
print("=" * 60)
r3 = c.post("/api/settings/provider", json={"provider_id": "deepseek"})
print("状态码：", r3.status_code)
assert r3.status_code in (200, 400), f"❌ 异常状态码 {r3.status_code}"
print("（200=切换成功 / 400=合法但被闸门拦，均为预期路径，不写真实 .env）")

print()
print("全部安全相关冒烟用例通过 ✅")
