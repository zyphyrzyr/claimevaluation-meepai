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

from ..config import (CAUSE_TRADEMARK, SUPPORTED_CAUSE_TYPES,
                      GOAL_TYPES, RUNTIME_DIR)
from .pkulaw_api import CAUSE_SEARCH_PROFILE, TOOL_ENDPOINTS, five_years_ago

DATA_DIR = RUNTIME_DIR  # 挂在 config.DATA_DIR 下，随 SOFT_IP_DATA_DIR 一起被测试隔离


def _queries_path(case_id: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"pkulaw_queries_{case_id}.json"


def _results_path(case_id: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"pkulaw_results_{case_id}.json"


# ============================================================
# 查询生成
# ============================================================

def _require_supported_cause(cause_type: str) -> str:
    """
    案由闸。

    旧实现把查询词写死成商标（"商标法 商标注册 撤三"、"驰名商标认定和保护规定"），
    与 v4 的三案由支持直接冲突：著作权案子会领到一份商标检索计划。这里与
    core/mock、core/moot_court/prompts、core/evidence_review 保持同一原则——
    未知案由显式报错，绝不静默回落。
    """
    if cause_type not in CAUSE_SEARCH_PROFILE:
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")
    return cause_type


def generate_all_queries(case_id: str, cause_type: str = CAUSE_TRADEMARK,
                         goal_type: str = "要钱", case_description: str = "") -> Dict:
    """
    按案由与业务目标生成北大法宝检索查询计划。

    查询词一律取自 pkulaw_api.CASE_SEARCH_PROFILE，此处不再内联任何案由相关字面量，
    避免两张表各改各的、改了一处漏了另一处。
    """
    _require_supported_cause(cause_type)
    if goal_type not in GOAL_TYPES:
        raise ValueError(f"不支持的业务目标：{goal_type}；当前支持 {GOAL_TYPES}")

    prof = CAUSE_SEARCH_PROFILE[cause_type]
    queries = {
        "case_id": case_id,
        "cause_type": cause_type,
        "goal_type": goal_type,
        "generated_at": datetime.now().isoformat(),
        "dimensions": {}
    }

    # 1.1 权利基础 → 法条检索
    queries["dimensions"]["1.1_权利基础"] = {
        "label": "权利基础",
        "searches": [
            {
                "id": "law_core",
                "type": "law_search",
                "tool": "search_article",
                "query": prof["rights_query"],
                "lib": "中央",
                "size": 5,
                "purpose": f"检索{cause_type}权利基础核心法条"
            },
            {
                "id": "law_article",
                "type": "law_article",
                "tool": "get_article",
                "title": prof["rights_law_title"],
                "number": prof["rights_law_article"],
                "purpose": f"直接取{cause_type}核心请求权基础条文原文"
            },
        ]
    }

    # 1.2 侵权认定 → 法条 + 类案
    queries["dimensions"]["1.2_侵权认定"] = {
        "label": "侵权认定",
        "searches": [
            {
                "id": "law_infringement",
                "type": "law_search",
                "tool": "search_article",
                "query": prof["infringement_query"],
                "lib": "中央",
                "size": 5,
                "purpose": f"检索{cause_type}侵权认定法条"
            },
            {
                "id": "case_infringement",
                "type": "case_search",
                "tool": "search_case",
                "query": prof["infringement_query"],
                "case_type": "民事案件",
                "doc_type": "判决书",
                "size": 5,
                "purpose": f"检索{cause_type}类案，验证侵权认定标准"
            }
        ]
    }

    # 1.3 诉讼程序 → 法条检索（程序问题三案由通用，无需案由画像）
    queries["dimensions"]["1.3_诉讼程序"] = {
        "label": "诉讼程序",
        "searches": [
            {
                "id": "law_limitation",
                "type": "law_search",
                "tool": "search_article",
                "query": "知识产权 诉讼时效 管辖法院 主体适格 前置程序 民事诉讼法",
                "lib": "中央",
                "size": 5,
                "purpose": "检索诉讼程序相关法条"
            }
        ]
    }

    # 1.4 模拟法庭 → 被告抗辩类案
    queries["dimensions"]["1.4_模拟法庭"] = {
        "label": "模拟法庭",
        "searches": [
            {
                "id": "case_defense",
                "type": "case_search",
                "tool": "search_case",
                "query": prof["defense_query"],
                "case_type": "民事案件",
                "size": 5,
                "purpose": f"检索{cause_type}常见抗辩路径，供模拟法庭被告方参考"
            }
        ]
    }

    # 2.1 业务预期：按目标分流——要钱查判赔类案，要名查首案
    if goal_type == "要钱":
        queries["dimensions"]["2.1_判赔规模"] = {
            "label": "判赔规模",
            "searches": [
                {
                    "id": "case_damages",
                    "type": "case_search",
                    "tool": "search_case",
                    "query": prof["damages_query"],
                    "case_type": "民事案件",
                    "doc_type": "判决书",
                    # 与 pkulaw_api.search_for_financial 同源的时间窗口——
                    # 计划与执行用同一个函数算，避免「计划写五年、执行写死 2020-01-01」
                    "decision_date_start": five_years_ago(),
                    "size": 5,
                    "purpose": "检索近年判赔类案，校准判赔区间"
                }
            ],
            "note": "回款能力由企查查被告画像支撑，不走北大法宝"
        }
    else:
        queries["dimensions"]["2.1_判例价值"] = {
            "label": "判例价值",
            "searches": [
                {
                    "id": "case_precedent",
                    "type": "case_search",
                    "tool": "search_case",
                    "query": (case_description[:200] + " " if case_description else "")
                             + prof["precedent_query"],
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
