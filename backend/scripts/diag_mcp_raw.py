"""
原始 MCP 响应诊断：
打印 QCC / 北大法宝 底层 _rpc_call 的真实返回，区分
「API 真返回空」vs「解析取错字段」vs「错误被吞」。
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
    print()


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

    print("### 北大法宝: search_article(trademark rights_query) ###")
    try:
        r = pkulaw_api._rpc_call("search_article", {
            "text": "商标专用权 注册商标 有效期 续展 撤销 驰名商标",
            "lib": "中央", "timeliness": "现行有效", "size": 5
        })
        show("pkulaw search_article", r)
        items = pkulaw_api._extract_items(r)
        print(f"[extract_items] count={len(items)}")
        if items:
            print("[first item keys]:", list(items[0].keys()) if isinstance(items[0], dict) else type(items[0]))
        print()
    except Exception as e:
        print(f"[ERROR] pkulaw call: {e}\n")

    print("### 北大法宝: search_article 带更简单的查询 ###")
    try:
        r = pkulaw_api._rpc_call("search_article", {"text": "商标法", "size": 3})
        show("pkulaw search_article(simple)", r)
        items = pkulaw_api._extract_items(r)
        print(f"[extract_items] count={len(items)}")
        print()
    except Exception as e:
        print(f"[ERROR] pkulaw call: {e}\n")


if __name__ == "__main__":
    main()
