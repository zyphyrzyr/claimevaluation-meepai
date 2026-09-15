"""
鉴权变体实验：排除 401 是「格式问题」还是「token 失效」。
对 QCC / 北大法宝 各试三种 Authorization 写法，看哪种能过。
"""
import json
import urllib.request

# 直接从 .env 读，确认实际下发的 token（脱敏打印）
def read_env():
    vals = {}
    p = "/Users/zhaoyirui/WorkBuddy/2026-08-25-17-14-24/soft-ip-v4/.env"
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

env = read_env()
qcc = env.get("QCC_API_TOKEN", "")
pku = env.get("PKULAW_API_TOKEN", "")

def mask(t):
    return (t[:6] + "…" + t[-4:]) if len(t) > 12 else "***"

print(f"QCC token(read): {mask(qcc)} (len={len(qcc)})")
print(f"PKU token(read): {mask(pku)} (len={len(pku)})")
print()

QCC_URL = "https://agent.qcc.com/mcp/company/stream"
PKU_URL = "https://apim-gateway.pkulaw.com/mcp-law-search-service"

def try_auth(label, url, token, auth_value):
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_company_by_query",
                   "arguments": {"searchKey": "华为技术有限公司"}},
    }
    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return f"HTTP {resp.status} (OK)"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return f"ERR: {str(e)[:120]}"

print("### QCC variants ###")
print(" A Bearer<prefix> :", try_auth("A", QCC_URL, qcc, f"Bearer {qcc}"))
print(" B raw token      :", try_auth("B", QCC_URL, qcc, qcc))
print(" C bearer lowercase:", try_auth("C", QCC_URL, qcc, f"bearer {qcc}"))
print()

PKU_PAYLOAD = {
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "search_article",
               "arguments": {"text": "商标法", "size": 3}},
}
def try_auth_pku(label, url, token, auth_value):
    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(PKU_PAYLOAD).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return f"HTTP {resp.status} (OK)"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return f"ERR: {str(e)[:120]}"

print("### 北大法宝 variants ###")
print(" A Bearer<prefix> :", try_auth_pku("A", PKU_URL, pku, f"Bearer {pku}"))
print(" B raw token      :", try_auth_pku("B", PKU_URL, pku, pku))
print(" C bearer lowercase:", try_auth_pku("C", PKU_URL, pku, f"bearer {pku}"))
