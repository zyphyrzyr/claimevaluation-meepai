"""
L0 模拟法庭外部依据聚合（context_sources）+ 三方注入

钉住四件事：
  1. 内嵌模式复用评估流程北大法宝落盘结果（load_results），不重复调用
  2. 经验库 RAG 召回并入依据块
  3. 独立演练（无 case_id）不触发 on-demand，类案缺失时显式标注「不得虚构案号」
  4. 依据块注入三方 system prompt（plaintiff/defendant/judge 都能看到）
"""

import pytest

from core.moot_court import context_sources
from core.moot_court.prompts import build_system_prompt
from core.moot_court.procedure import MootCourtProcedure


PKULAW_RESULTS = {
    "law_results": [
        {"title": "《商标法》第五十七条", "content": "有下列行为之一的，均属侵犯注册商标专用权……"},
        {"title": "《商标法》第六十三条", "content": "侵犯商标专用权的赔偿数额……"},
    ],
    "case_results": [
        {"title": "某某商标侵权纠纷", "court": "最高人民法院", "date": "2023-05-01",
         "summary": "商标近似以整体观感比对为准。"},
    ],
}

EXPERIENCE_RECALL = {
    "context": "【经验库·商标】公证购买取证要点：先验真品后购假货……",
    "refs": [{"id": "e1", "title": "公证购买取证要点", "score": 0.9,
              "scope": "global", "source_type": "exp"}],
}


@pytest.fixture
def _patch(monkeypatch):
    # load_results：有 case_id 返回北大法宝结果，无 case_id（独立演练）返回 None —— 不 on-demand
    monkeypatch.setattr(
        "core.pkulaw.pkulaw_integration.load_results",
        lambda case_id: PKULAW_RESULTS if case_id else None,
    )
    monkeypatch.setattr(
        "core.knowledge.recall_for_context",
        lambda db, case_id, query, top_k=3, user_id=None: EXPERIENCE_RECALL,
    )


def test_gather_reuses_pkulaw_and_experience(_patch):
    ctx = context_sources.gather_legal_context(
        "case-123", "商标侵权", "被告销售近似商标商品", db=object(), user_id="u1")
    # 法定基准（兜底，始终有）
    assert "法定赔偿区间" in ctx["block"]
    # 北大法宝法条/类案复用
    assert "《商标法》第五十七条" in ctx["block"]
    assert "某某商标侵权纠纷" in ctx["block"]
    assert ctx["pkulaw_laws"] and ctx["pkulaw_cases"]
    # 经验库召回并入
    assert "公证购买取证要点" in ctx["block"]
    assert ctx["refs"] == EXPERIENCE_RECALL["refs"]


def test_standalone_no_case_id_no_on_demand(_patch):
    # case_id=None → 不调北大法宝（lambda 返回 None），块里标注不得虚构，且不抛
    ctx = context_sources.gather_legal_context(
        None, "商标侵权", "被告销售近似商标商品", db=object(), user_id="u1")
    assert ctx["pkulaw_laws"] == []
    assert ctx["pkulaw_cases"] == []
    assert "不得虚构案号" in ctx["block"]
    # 法定基准仍在，经验库仍在（独立演练仍可用）
    assert "法定赔偿区间" in ctx["block"]
    assert "公证购买取证要点" in ctx["block"]


def test_unknown_cause_raises():
    with pytest.raises(ValueError):
        context_sources.gather_legal_context("c", "专利侵权", "", db=None)


def test_legal_context_injected_to_all_roles(_patch):
    ctx = context_sources.gather_legal_context(
        "case-123", "商标侵权", "被告销售近似商标商品", db=object(), user_id="u1")
    block_marker = "《商标法》第五十七条"
    assert block_marker in ctx["block"]
    proc = MootCourtProcedure(
        case_description="x", cause_type="商标侵权", shared_legal_context=ctx["block"])
    for agent in (proc.plaintiff, proc.defendant, proc.judge):
        assert block_marker in agent.system_prompt, agent.role_name


def test_build_system_prompt_without_block_has_no_legal_section():
    sys_p = build_system_prompt("plaintiff", "商标侵权")
    assert "可引用依据" not in sys_p
    sys_p2 = build_system_prompt("plaintiff", "商标侵权", "【可引用依据】测试")
    assert "可引用依据" in sys_p2
