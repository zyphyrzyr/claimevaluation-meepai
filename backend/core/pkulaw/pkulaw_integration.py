"""
北大法宝 MCP 集成模块
负责：
1. 各评估维度生成北大法宝检索查询
2. 存储检索结果（供 Streamlit 读取）
3. 法条/案号验证接口
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from ..config import RUNTIME_DIR

DATA_DIR = RUNTIME_DIR


def _queries_path(case_id: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"pkulaw_queries_{case_id}.json"


def _results_path(case_id: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"pkulaw_results_{case_id}.json"


# ============================================================
# 查询生成
# ============================================================

def generate_all_queries(case_id: str, case_description: str, deepseek_results: Dict) -> Dict:
    """
    根据各维度评估结果，生成完整的北大法宝检索查询计划
    deepseek_results = {
        "rights": {...}, "infringement": {...}, "procedure": {...},
        "financial": {...}, "precedent": {...}, "evidence": {...}
    }
    返回结构化查询计划
    """
    queries = {
        "case_id": case_id,
        "generated_at": datetime.now().isoformat(),
        "dimensions": {}
    }

    # 1.1 权利基础 → 法条检索
    rights = deepseek_results.get("rights", {})
    queries["dimensions"]["1.1_权利基础"] = {
        "label": "权利基础",
        "searches": [
            {
                "id": "law_trademark",
                "type": "law_search",
                "tool": "search_article",
                "query": "商标法 商标注册 注册商标 有效期 续展 撤三",
                "lib": "中央",
                "size": 5,
                "purpose": "检索商标法核心法条，验证商标权利基础"
            },
            {
                "id": "law_wellknown",
                "type": "law_keyword",
                "tool": "get_law_list",
                "title": "驰名商标认定和保护规定",
                "fulltext": "驰名商标 认定 跨类保护",
                "purpose": "检索驰名商标相关法规"
            }
        ]
    }

    # 1.2 侵权认定 → 类案检索
    infringement = deepseek_results.get("infringement", {})
    queries["dimensions"]["1.2_侵权认定"] = {
        "label": "侵权认定",
        "searches": [
            {
                "id": "case_infringement",
                "type": "case_search",
                "tool": "search_case",
                "query": "商标侵权 近似商标 混淆可能性 相同商品 类似商品",
                "case_type": "民事案件",
                "size": 5,
                "purpose": "检索商标侵权类案，验证侵权认定标准"
            }
        ]
    }

    # 1.3 诉讼程序 → 法条检索
    queries["dimensions"]["1.3_诉讼程序"] = {
        "label": "诉讼程序",
        "searches": [
            {
                "id": "law_limitation",
                "type": "law_search",
                "tool": "search_article",
                "query": "民事诉讼 诉讼时效 管辖 仲裁协议 主体适格",
                "lib": "中央",
                "size": 5,
                "purpose": "检索诉讼程序相关法条"
            }
        ]
    }

    # 2.1 财务回报 → 暂不检索（后续用企查查）
    queries["dimensions"]["2.1_财务回报"] = {
        "label": "财务回报",
        "searches": [],
        "note": "暂不通过北大法宝检索，后续接入企查查"
    }

    # 2.2 判例价值 → 首案检索
    queries["dimensions"]["2.2_判例价值"] = {
        "label": "判例价值",
        "searches": [
            {
                "id": "case_precedent",
                "type": "case_search",
                "tool": "search_case",
                "query": "商标侵权 首案 新型侵权 指导性案例",
                "case_type": "民事案件",
                "size": 10,
                "purpose": "检索同类在先判决，判断首案潜力"
            }
        ]
    }

    # 验证步骤（评估后运行）
    queries["validation"] = {
        "label": "法条与案号验证",
        "steps": [
            {
                "id": "validate_provisions",
                "tool": "adjust_provisions",
                "purpose": "验证评估报告中引用的法条是否真实有效、是否现行有效"
            },
            {
                "id": "recognize_laws",
                "tool": "law_recognition",
                "purpose": "识别评估报告中的法规引用，核验准确性"
            },
            {
                "id": "recognize_cases",
                "tool": "anhao_recognition",
                "purpose": "识别评估报告中的案号引用，验证真实性"
            }
        ]
    }

    # 保存查询计划
    qpath = _queries_path(case_id)
    qpath.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")

    return queries


# ============================================================
# 结果存取
# ============================================================

def save_results(case_id: str, results: Dict) -> None:
    """保存 MCP 检索结果"""
    rpath = _results_path(case_id)
    existing = {}
    if rpath.exists():
        try:
            existing = json.loads(rpath.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(results)
    existing["updated_at"] = datetime.now().isoformat()
    rpath.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results(case_id: str) -> Optional[Dict]:
    """加载 MCP 检索结果"""
    rpath = _results_path(case_id)
    if rpath.exists():
        try:
            return json.loads(rpath.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def get_validation_status(case_id: str) -> Dict:
    """获取验证状态"""
    results = load_results(case_id)
    if not results:
        return {"laws_validated": False, "cases_validated": False, "provisions_validated": False}

    return {
        "laws_validated": bool(results.get("law_recognition")),
        "cases_validated": bool(results.get("anhao_recognition")),
        "provisions_validated": bool(results.get("adjust_provisions")),
        "total_sources": len(results.get("sources", [])),
        "updated_at": results.get("updated_at", "")
    }


def get_dimension_results(case_id: str, dimension: str) -> Optional[Dict]:
    """获取某个维度的检索结果"""
    results = load_results(case_id)
    if not results:
        return None
    return results.get(dimension)


def format_for_report(case_id: str) -> str:
    """生成报告中的北大法宝检索结果章节"""
    results = load_results(case_id)
    if not results:
        return ""

    parts = []

    # 法条检索结果
    laws = results.get("law_results", [])
    if laws:
        parts.append("### 相关法律法规（北大法宝检索）\n")
        for law in laws[:10]:
            title = law.get("title", law.get("name", ""))
            content = law.get("content", law.get("text", ""))
            if title:
                parts.append(f"**{title}**")
            if content:
                parts.append(content)
            parts.append("")

    # 类案检索结果
    cases = results.get("case_results", [])
    if cases:
        parts.append("### 类案参考（北大法宝检索）\n")
        for i, case in enumerate(cases[:5], 1):
            title = case.get("title", case.get("name", ""))
            court = case.get("court", case.get("courthouse_name", ""))
            date = case.get("date", case.get("decision_date", ""))
            summary = case.get("summary", case.get("abstract", ""))
            parts.append(f"**{i}. {title}**")
            if court:
                parts.append(f"- 审理法院: {court}")
            if date:
                parts.append(f"- 日期: {date}")
            if summary:
                parts.append(f"- 摘要: {summary[:200]}")
            parts.append("")

    # 验证结果
    valid = results.get("validation", {})
    if valid:
        parts.append("### 法条与案号验证\n")
        if valid.get("provisions"):
            parts.append(f"**法条验证**: {valid['provisions']}")
        if valid.get("laws"):
            parts.append(f"**法规识别**: {valid['laws']}")
        if valid.get("cases"):
            parts.append(f"**案号识别**: {valid['cases']}")

    return "\n".join(parts)
