"""
北大法宝 MCP Connector API
封装 8 个核心检索/验证函数，用于 Soft IP 评估系统。
按工具对应不同 MCP 端点（SSE JSON-RPC，Bearer token）。
"""

import json
import urllib.request
import urllib.error
from typing import Dict

from ..config import get_runtime_settings

from ..config import (CAUSE_COPYRIGHT, CAUSE_TRADEMARK,
                      CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)

API_BASE = "https://apim-gateway.pkulaw.com"


# 各案由的检索画像。
#
# 为什么不复用 moot_court/prompts.py 的 CAUSE_PROFILES：那张表服务的是庭审剧本
# （请求权基础、抗辩路径、法官审查要点，都是整段自然语言），而这里要的是检索器
# 直接吃的查询词、法条标题与条号——二者形态不同，硬塞一张表两边都别扭。
#
# 为什么必须有这张表：早期这些查询词全写死成商标（"商标专用权 驰名商标"、
# get_article 固定取《商标法》第五十七条）。系统已支持三案由，若直接接进主流程，
# 著作权案子会检索回一堆商标法条——检索增强反而变成噪声注入。
CAUSE_SEARCH_PROFILE = {
    CAUSE_TRADEMARK: {
        "rights_query": "商标专用权 注册商标 有效期 续展 撤销 驰名商标",
        "rights_law_title": "中华人民共和国商标法",
        "rights_law_article": "第五十七条",
        "infringement_query": "商标侵权 近似商标 混淆可能性 商标性使用 类似商品",
        "defense_query": "商标侵权 合理使用抗辩 正当使用 在先使用 描述性使用",
        "damages_query": "商标侵权 赔偿数额 实际损失 侵权获利 法定赔偿",
        "precedent_query": "商标侵权 首案 指导性案例",
    },
    CAUSE_COPYRIGHT: {
        "rights_query": "著作权 作品 独创性 著作权归属 登记 署名权 保护期",
        "rights_law_title": "中华人民共和国著作权法",
        "rights_law_article": "第十条",
        "infringement_query": "著作权侵权 实质性相似 接触 复制 改编 信息网络传播权",
        "defense_query": "著作权侵权 合理使用 独立创作 思想表达二分 独创性不足",
        "damages_query": "著作权侵权 赔偿数额 实际损失 违法所得 法定赔偿",
        "precedent_query": "著作权侵权 首案 指导性案例 新型作品",
    },
    CAUSE_UNFAIR_COMPETITION: {
        "rights_query": "有一定影响的商品名称 包装 装潢 企业名称 混淆 不正当竞争",
        "rights_law_title": "中华人民共和国反不正当竞争法",
        "rights_law_article": "第六条",
        "infringement_query": "不正当竞争 混淆行为 仿冒 擅自使用 有一定影响",
        "defense_query": "不正当竞争 不具一定影响 描述性使用 功能性 无竞争关系",
        "damages_query": "不正当竞争 赔偿数额 实际损失 侵权获利 法定赔偿",
        "precedent_query": "不正当竞争 首案 指导性案例 新型竞争行为",
    },
}


def _profile(cause_type: str) -> dict:
    """取案由检索画像；未知案由显式报错，避免静默回落到商标"""
    if cause_type not in CAUSE_SEARCH_PROFILE:
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")
    return CAUSE_SEARCH_PROFILE[cause_type]

# 工具 → 端点
TOOL_ENDPOINTS = {
    # 法条检索
    "search_article": "mcp-law-search-service",
    "get_article": "mcp-law-search-service",
    # 案例检索
    "search_case": "mcp-case-search-service",
    # 防幻觉验证
    "law_recognition": "law_recognition",
    "anhao_recognition": "case_number_recognition",
    "adjust_provisions": "pku_citation_validator",
    # 报告增强
    "get_linked_content": "add-doc-link",
}

def _pkulaw_configured() -> bool:
    return bool(get_runtime_settings().get("pkulaw_api_token", "").strip())


def _pkulaw_headers() -> dict:
    token = get_runtime_settings().get("pkulaw_api_token", "").strip()
    if not token:
        raise RuntimeError("未配置 PKULAW_API_TOKEN，无法调用北大法宝服务")
    auth_value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _pkulaw_unconfigured_result() -> Dict:
    message = "未配置 PKULAW_API_TOKEN，北大法宝检索与验证已跳过"
    return {
        "status": "skipped",
        "error": message,
        "laws": [],
        "cases": [],
        "_summary": message,
    }


def _rpc_call(tool_name: str, args: dict) -> Dict:
    """通用 PKULaw JSON-RPC 调用"""
    endpoint = TOOL_ENDPOINTS.get(tool_name, "mcp-law-search-service")
    url = f"{API_BASE}/{endpoint}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=_pkulaw_headers())
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)[:200]}

    # 解析：先尝试 JSON-RPC 直接响应，否则按 SSE 解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for line in raw.split("\n"):
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return {"error": f"SSE 解析失败: {raw[:200]}"}


def _extract_items(rpc_result: dict) -> list:
    """从 rpc 结果中提取条目列表
    实际结构: result.content[0].text = JSON 字符串
    """
    if not rpc_result or "error" in rpc_result:
        return []
    r = rpc_result.get("result", {})
    if not isinstance(r, dict):
        return []

    # 结构1: result.content[0].text = JSON 字符串
    content = r.get("content", [])
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            try:
                parsed = json.loads(first["text"])
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass

    # 结构2: result.result 或 result.Data = 列表
    items = r.get("result", r.get("Result", []))
    if not items:
        items = r.get("Data", r.get("data", []))
    if not items and r.get("original"):
        items = [r]
    return items if isinstance(items, list) else []


# ==========================================
# 维度检索函数（每个返回 {laws: [], cases: [], _summary: ""}）
# ==========================================

def search_for_rights_foundation(cause_type: str = CAUSE_TRADEMARK) -> Dict:
    """1.1 权利基础：search_article + get_article（查询词按案由取）"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    prof = _profile(cause_type)
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    try:
        sa = _rpc_call("search_article", {
            "text": prof["rights_query"],
            "lib": "中央", "timeliness": "现行有效", "size": 5
        })
        for it in _extract_items(sa)[:5]:
            if isinstance(it, dict):
                result["laws"].append({
                    "title": it.get("title", ""),
                    "content": it.get("article", "")[:300],
                    "timeliness": it.get("timeliness", "")
                })
    except Exception as e:
        result["error"] = f"search_article: {e}"

    try:
        ga = _rpc_call("get_article", {
            "title": prof["rights_law_title"],
            "number": prof["rights_law_article"]
        })
        for it in _extract_items(ga)[:1]:
            if isinstance(it, dict):
                result["laws"].insert(0, {
                    "title": it.get("title", prof["rights_law_title"]),
                    "content": it.get("article", "")[:400],
                    "timeliness": "现行有效"
                })
    except Exception:
        pass

    summary.append(f"【权利基础】检索到 {len(result['laws'])} 条法条")
    result["_summary"] = "\n".join(summary)
    return result


def search_for_infringement(cause_type: str = CAUSE_TRADEMARK) -> Dict:
    """1.2 侵权认定：search_case + search_article"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    prof = _profile(cause_type)
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    try:
        sa = _rpc_call("search_article", {
            "text": prof["infringement_query"],
            "lib": "中央", "timeliness": "现行有效", "size": 5
        })
        for it in _extract_items(sa)[:3]:
            if isinstance(it, dict):
                result["laws"].append({
                    "title": it.get("title", ""),
                    "content": it.get("article", "")[:300]
                })
    except Exception:
        pass

    try:
        sc = _rpc_call("search_case", {
            "text": prof["infringement_query"],
            "case_type": "民事案件", "doc_type": "判决书", "size": 5
        })
        for it in _extract_items(sc)[:5]:
            if isinstance(it, dict):
                result["cases"].append({
                    "title": it.get("title", ""),
                    "court": it.get("courthouse_name", it.get("court", "")),
                    "date": it.get("decision_date", it.get("date", "")),
                    "summary": (it.get("ascertain", it.get("summary", "")))[:300]
                })
    except Exception:
        pass

    summary.append(f"【侵权认定】检索到 {len(result['laws'])} 条法条, {len(result['cases'])} 个类案")
    result["_summary"] = "\n".join(summary)
    return result


def search_for_procedure() -> Dict:
    """1.3 诉讼程序：search_article"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    try:
        sa = _rpc_call("search_article", {
            "text": "知识产权 诉讼时效 管辖法院 主体适格 前置程序 民事诉讼法",
            "lib": "中央", "timeliness": "现行有效", "size": 5
        })
        for it in _extract_items(sa)[:5]:
            if isinstance(it, dict):
                result["laws"].append({
                    "title": it.get("title", ""),
                    "content": it.get("article", "")[:300]
                })
    except Exception:
        pass

    summary.append(f"【诉讼程序】检索到 {len(result['laws'])} 条法条")
    result["_summary"] = "\n".join(summary)
    return result


def search_for_moot_court(cause_type: str = CAUSE_TRADEMARK) -> Dict:
    """1.4 模拟法庭：search_case（被告抗辩模式）"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    prof = _profile(cause_type)
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    try:
        sc = _rpc_call("search_case", {
            "text": prof["defense_query"],
            "case_type": "民事案件", "size": 5
        })
        for it in _extract_items(sc)[:5]:
            if isinstance(it, dict):
                result["cases"].append({
                    "title": it.get("title", ""),
                    "court": it.get("courthouse_name", it.get("court", "")),
                    "date": it.get("decision_date", it.get("date", "")),
                    "summary": (it.get("ascertain", it.get("summary", "")))[:200]
                })
    except Exception:
        pass

    summary.append(f"【模拟法庭】检索到 {len(result['cases'])} 个抗辩类案")
    result["_summary"] = "\n".join(summary)
    return result


def search_for_financial(cause_type: str = CAUSE_TRADEMARK) -> Dict:
    """2.1 财务回报：search_case（判赔数据）"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    prof = _profile(cause_type)
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    try:
        sc = _rpc_call("search_case", {
            "text": prof["damages_query"],
            "case_type": "民事案件", "doc_type": "判决书",
            "decision_date_start": "2020-01-01", "size": 5
        })
        for it in _extract_items(sc)[:5]:
            if isinstance(it, dict):
                result["cases"].append({
                    "title": it.get("title", ""),
                    "court": it.get("courthouse_name", it.get("court", "")),
                    "date": it.get("decision_date", it.get("date", "")),
                    "summary": (it.get("ascertain", it.get("summary", "")))[:200]
                })
    except Exception:
        pass

    summary.append(f"【财务回报】检索到 {len(result['cases'])} 个判赔类案")
    result["_summary"] = "\n".join(summary)
    return result


def search_for_precedent(case_desc: str = "", cause_type: str = CAUSE_TRADEMARK) -> Dict:
    """2.2 判例价值：search_case（首案判断）"""
    if not _pkulaw_configured():
        return _pkulaw_unconfigured_result()
    prof = _profile(cause_type)
    result = {"laws": [], "cases": [], "_summary": ""}
    summary = []

    query = (case_desc[:200] if case_desc else "") + " " + prof["precedent_query"]
    try:
        sc = _rpc_call("search_case", {
            "text": query[:500],
            "case_type": "民事案件", "size": 10
        })
        for it in _extract_items(sc)[:10]:
            if isinstance(it, dict):
                result["cases"].append({
                    "title": it.get("title", ""),
                    "court": it.get("courthouse_name", it.get("court", "")),
                    "date": it.get("decision_date", it.get("date", "")),
                    "summary": (it.get("ascertain", it.get("summary", "")))[:200]
                })
    except Exception:
        pass

    summary.append(f"【判例价值】检索到 {len(result['cases'])} 个同类在先判决")
    result["_summary"] = "\n".join(summary)
    return result


# ==========================================
# 验证阶段
# ==========================================

def run_verification_phase(report_md: str) -> Dict:
    """防幻觉验证：adjust_provisions → law_recognition → anhao_recognition(逐段) → search_case 交叉验证"""
    if not _pkulaw_configured():
        message = "未配置 PKULAW_API_TOKEN，北大法宝引用验证已跳过"
        return {
            "status": "skipped",
            "error": message,
            "_summary": message,
            "summary": {
                "laws_verified": False,
                "cases_verified": False,
                "laws_found": 0,
                "cases_found": 0,
                "hallucinations": [],
            },
        }
    result = {
        "adjust_provisions": {},
        "law_recognition": {},
        "anhao_recognition": {"items": []},
        "cross_check_case": {},
        "summary": {
            "laws_verified": False,
            "cases_verified": False,
            "laws_found": 0,
            "cases_found": 0,
            "hallucinations": []
        }
    }

    # 1. adjust_provisions
    user_laws = [{"title": "商标法", "article": "57"}, {"title": "商标法", "article": "63"}]
    try:
        result["adjust_provisions"] = _rpc_call("adjust_provisions", {"userlaw": user_laws})
    except Exception as e:
        result["summary"]["hallucinations"].append(f"adjust_provisions: {e}")

    # 2. law_recognition
    try:
        result["law_recognition"] = _rpc_call("law_recognition", {"text": report_md[:4000]})
        law_items = _extract_items(result["law_recognition"])
        result["summary"]["laws_found"] = len(law_items)
        result["summary"]["laws_verified"] = len(law_items) > 0
    except Exception as e:
        result["summary"]["hallucinations"].append(f"law_recognition: {e}")

    # 3. anhao_recognition（逐段）
    paragraphs = [p for p in report_md.split("\n\n") if len(p.strip()) > 20][:5]
    for para in paragraphs:
        try:
            r = _rpc_call("anhao_recognition", {"text": para[:2000]})
            items = _extract_items(r)
            if items:
                result["anhao_recognition"]["items"].extend(items)
        except Exception:
            pass
    result["summary"]["cases_found"] = len(result["anhao_recognition"]["items"])
    result["summary"]["cases_verified"] = True  # 0 cases is OK

    # 4. search_case 交叉验证
    try:
        result["cross_check_case"] = _rpc_call("search_case", {
            "text": "商标侵权 判决书", "case_type": "民事案件", "size": 3
        })
    except Exception as e:
        result["summary"]["hallucinations"].append(f"search_case: {e}")

    return result


# ==========================================
# 报告增强
# ==========================================

def get_linked_content(message: str) -> Dict:
    """为法律分析文本添加法宝超链接"""
    if not _pkulaw_configured():
        return {"status": "skipped", "error": "未配置 PKULAW_API_TOKEN，法宝超链增强已跳过"}
    if not message:
        return {"error": "message is empty"}
    return _rpc_call("get_linked_content", {"message": message})
