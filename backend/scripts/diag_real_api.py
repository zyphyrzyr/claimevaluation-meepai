"""
真实链路诊断：把 reasoner / QCC / 北大法宝的原始响应打出来，定位「分数为何全 None」。
仅用于排查，不进回归。
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    import os
    os.environ.pop(_k, None)

from core.config import get_runtime_settings

s = get_runtime_settings()

print("=" * 66)
print("A. Deepseek-reasoner 原始响应（强模型节点）")
print("=" * 66)
url = f"{s['llm_base_url'].rstrip('/')}/v1/chat/completions"
payload = {"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "只回复两个字：正常"}], "max_tokens": 60, "stream": False}
req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
req.add_header("Authorization", f"Bearer {s['llm_api_key']}")
req.add_header("Content-Type", "application/json")
try:
    raw = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
    msg = raw["choices"][0]["message"]
    print("message 字段:", list(msg.keys()))
    print("content       :", repr(msg.get("content")))
    print("reasoning     :", repr((msg.get("reasoning_content") or ""))[:120])
except Exception as e:
    print("ERR:", type(e).__name__, e)

print()
print("=" * 66)
print("B. 企查查 MCP 原始结果（回款能力）")
print("=" * 66)
from core import qcc_api
prof = qcc_api.search_for_financial_qcc_full({"company_name": "华为技术有限公司"})
print("顶层键:", list(prof.keys()) if isinstance(prof, dict) else type(prof))
print("status:", prof.get("status") if isinstance(prof, dict) else None)
print("error :", prof.get("error") if isinstance(prof, dict) else None)
print("metrics:", json.dumps(prof.get("metrics"), ensure_ascii=False)[:600] if isinstance(prof, dict) else None)

print()
print("=" * 66)
print("C. 北大法宝 MCP 原始结果（法条检索）")
print("=" * 66)
from core.pkulaw import pkulaw_api
res = pkulaw_api.search_for_rights_foundation("商标侵权")
print("顶层键:", list(res.keys()) if isinstance(res, dict) else type(res))
print("status:", res.get("status") if isinstance(res, dict) else None)
print("error :", res.get("error") if isinstance(res, dict) else None)
print("全文(前800):", json.dumps(res, ensure_ascii=False)[:800] if isinstance(res, dict) else None)
