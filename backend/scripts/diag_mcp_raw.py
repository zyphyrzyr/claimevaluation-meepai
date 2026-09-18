"""
北大法宝 / QCC 原始 MCP 响应诊断（本地运行，需联网 + 已配置 token）。

用途：当「北大法宝调用失败 / 报告里没有法条类案」时，用本脚本直接打底层
`_rpc_call`，把真实返回原样打印出来，并给出 `_rpc_failed` 的判定结论，
一眼区分四种情况：
  1. token 没配            → 直接提示去配置 PKULAW_API_TOKEN
  2. HTTP/网络层失败        → err 是字符串（如 "HTTP Error 401..."），_rpc_failed 能抓到
  3. 200 但 JSON-RPC 错误   → err 是结构化对象 {"error":{"code":401,"message":...}}
                              （旧逻辑会漏掉，现已修；本脚本也会显式打印出来）
  4. 200 但字段取错/解析错  → 返回 ok 形状但 items 为空，需要看 raw 定位

运行：
  cd backend
  /path/to/venv/python scripts/diag_mcp_raw.py
"""
import json
import sys

sys.path.insert(0, "/Users/zhaoyirui/WorkBuddy/2026-08-25-17-14-24/soft-ip-v4/backend")

from core import qcc_api
from core.pkulaw import pkulaw_api


def show(label, raw, limit=3500):
    print("=" * 70)
    print(f"[RAW] {label}")
    print("=" * 70)
    txt = json.dumps(raw, ensure_ascii=False, default=str)
    if len(txt) > limit:
        txt = txt[:limit] + f"\n... (truncated, total {len(txt)} chars)"
    print(txt)
    # 关键：打印 _rpc_failed 的判定，避免「看起来成功其实失败」
    verdict = pkulaw_api._rpc_failed(raw)
    if verdict:
        print(f"\n[_rpc_failed 判定] ❌ {verdict}")
    else:
        print("\n[_rpc_failed 判定] ✅ 未识别到错误（视为成功/跳过）")
    print()


def pkulaw_call(label, tool, args):
    print(f"### 北大法宝: {label} ###")
    if not pkulaw_api._pkulaw_configured():
        print("⚠️  PKULAW_API_TOKEN 未配置 —— 这是「调用失败」最常见的原因！\n"
              "   去运行环境配置 PKULAW_API_TOKEN 后重试。\n")
        return
    try:
        r = pkulaw_api._rpc_call(tool, args)
        show(f"pkulaw {tool}", r)
        items = pkulaw_api._extract_items(r)
        print(f"[extract_items] count={len(items)}")
        if items:
            print("[first item keys]:",
                  list(items[0].keys()) if isinstance(items[0], dict) else type(items[0]))
        print()
    except Exception as e:
        print(f"[ERROR] pkulaw call: {e}\n")


def main():
    print("### QCC: get_company_by_query(华为技术有限公司) ###")
    try:
        r = qcc_api._rpc_call("get_company_by_query", {"searchKey": "华为技术有限公司"}, server="company")
        show("qcc get_company_by_query", r)
        items = qcc_api._extract_items(r)
        print(f"[extract_items] count={len(items)}")
        print()
    except Exception as e:
        print(f"[ERROR] qcc call: {e}\n")

    pkulaw_call("search_article(商标权基础检索)", "search_article", {
        "text": "商标专用权 注册商标 有效期 续展 撤销 驰名商标",
        "lib": "中央", "timeliness": "现行有效", "size": 5,
    })
    pkulaw_call("search_article(简单查询)", "search_article", {"text": "商标法", "size": 3})

    print("### 北大法宝: run_verification_phase(鉴权快检) ###")
    if pkulaw_api._pkulaw_configured():
        try:
            v = pkulaw_api.run_verification_phase("# 测试报告\n《商标法》第五十七条")
            show("pkulaw run_verification_phase", v)
            print(f"[status] {v.get('status')}  [error] {v.get('error')}")
        except Exception as e:
            print(f"[ERROR] verification: {e}\n")
    else:
        print("（token 未配置，跳过核验阶段）\n")


if __name__ == "__main__":
    main()
