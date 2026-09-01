"""
L0 案由法律依据表

这张表是「事实性基准」，不是模型判断——所以每个数字都该被测试钉住。
一旦有人改动法条口径（比如把著作权法定赔偿的下限删掉），这里会立刻报出来。

另外两处重点：
1. 三案由之间**确实有差异**，不是复制粘贴。差异才是这张表存在的理由，
   如果哪天三个案由的赔偿区间被"统一"了，测试应当失败。
2. 节点 prompt 真的把基准带进去了。Mock 模式下这些节点根本不走 prompt，
   所以必须单独验证拼装结果，否则等于没接。
"""
import dataclasses

import pytest

from core import evaluate_nodes, legal_basis
from core.case_context import CaseContext
from core.config import (CAUSE_COPYRIGHT, CAUSE_TRADEMARK,
                         CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)


def _ctx(**overrides) -> CaseContext:
    kwargs = dict(
        case_id="basis-1",
        case_description="原告权利受侵害，被告在电商平台销售侵权商品。",
        cause_type=CAUSE_TRADEMARK,
        goal_type="要钱",
    )
    kwargs.update(overrides)
    return CaseContext(**kwargs)


# ============================================================
# 1. 注册表完整性
# ============================================================

class TestRegistry:
    def test_all_supported_causes_are_registered(self):
        for cause in SUPPORTED_CAUSE_TYPES:
            assert cause in legal_basis.LEGAL_BASIS, f"{cause} 缺少法律依据表"

    def test_unknown_cause_raises_instead_of_falling_back(self):
        """
        静默回落的后果是拿商标的规则去评一个别的案由的案件：
        输出的每个数字都看似合理、实则全错，而且不会有任何报错。
        """
        with pytest.raises(ValueError, match="不支持的案由"):
            legal_basis.get_legal_basis("专利侵权")
        with pytest.raises(ValueError, match="不支持的案由"):
            legal_basis.get_legal_basis("")
        with pytest.raises(ValueError, match="不支持的案由"):
            legal_basis.get_legal_basis(None)

    def test_every_field_is_populated(self):
        """空字段会静默退化成通用 prompt，等于该案由没接法律依据"""
        for cause, basis in legal_basis.LEGAL_BASIS.items():
            for field in dataclasses.fields(basis):
                value = getattr(basis, field.name)
                assert value, f"{cause}.{field.name} 不能为空"
                if isinstance(value, tuple):
                    assert all(item for item in value), (
                        f"{cause}.{field.name} 里不能有空项")

    def test_every_cause_cites_its_sources(self):
        """每个案由至少引两部规范：一部实体法、一部司法解释"""
        for cause, basis in legal_basis.LEGAL_BASIS.items():
            assert len(basis.sources) >= 2, f"{cause} 法条出处不足，无法复核"


# ============================================================
# 2. 赔偿基准：三案由之间必须真的不一样
# ============================================================

class TestDamagesClause:
    def test_copyright_statutory_range_has_a_lower_bound(self):
        """著作权法定赔偿是三类里唯一有下限的（五百元以上五百万元以下）"""
        clause = legal_basis.damages_clause(CAUSE_COPYRIGHT)
        assert "五百元以上五百万元以下" in clause

    def test_trademark_range_has_no_lower_bound(self):
        clause = legal_basis.damages_clause(CAUSE_TRADEMARK)
        assert "五百万元以下" in clause
        assert "五百元以上" not in clause

    def test_unfair_range_is_limited_to_articles_6_and_9(self):
        """
        反法第十七条的法定赔偿**只**覆盖第六条（混淆）与第九条（商业秘密）。
        商业诋毁、互联网专条不适用——这是最容易搞错的一处。
        """
        clause = legal_basis.damages_clause(CAUSE_UNFAIR_COMPETITION)
        assert "第六条" in clause and "第九条" in clause
        assert "不适用" in clause
        assert "第十一条" in clause and "第十二条" in clause

    def test_punitive_damages_standard_differs_by_cause(self):
        """
        惩罚性赔偿的主观要件各不相同：
            商标 = 恶意；著作权 = 故意；不正当竞争 = 恶意且**仅限商业秘密**
        混用会直接导致判赔区间算错。
        """
        trademark = legal_basis.damages_clause(CAUSE_TRADEMARK)
        copyright_ = legal_basis.damages_clause(CAUSE_COPYRIGHT)
        unfair = legal_basis.damages_clause(CAUSE_UNFAIR_COMPETITION)

        assert "恶意" in trademark
        assert "故意" in copyright_
        assert "恶意" in unfair and "侵犯商业秘密" in unfair
        assert "混淆行为不适用惩罚性赔偿" in unfair

    def test_the_three_clauses_are_not_identical(self):
        """兜底断言：三个案由的赔偿基准若被"统一"了，说明差异化丢了"""
        clauses = {
            legal_basis.damages_clause(c) for c in SUPPORTED_CAUSE_TYPES}
        assert len(clauses) == 3

    def test_clause_names_its_cause(self):
        for cause in SUPPORTED_CAUSE_TYPES:
            assert cause in legal_basis.damages_clause(cause)


# ============================================================
# 3. 程序基准：时效与管辖
# ============================================================

class TestProcedureClause:
    def test_trademark_and_copyright_both_have_the_three_year_lookback(self):
        """
        商标与著作权都有「持续侵权 → 判停止侵权，赔偿额自起诉之日起向前推算三年」
        的特殊规则，但**触发条件不同**：商标要求"在注册商标专用权有效期限内"，
        著作权要求"在该著作权保护期内"。
        """
        trademark = legal_basis.procedure_clause(CAUSE_TRADEMARK)
        copyright_ = legal_basis.procedure_clause(CAUSE_COPYRIGHT)
        for clause in (trademark, copyright_):
            assert "向前推算三年计算" in clause
        assert "注册商标专用权有效期限" in trademark
        assert "著作权保护期" in copyright_

    def test_unfair_explicitly_warns_there_is_no_lookback_rule(self):
        """
        不正当竞争没有对应的司法解释，却是最容易被顺手套用的地方。
        """
        clause = legal_basis.procedure_clause(CAUSE_UNFAIR_COMPETITION)
        assert "没有" in clause and "向前推算三年" in clause
        assert "不要套用" in clause

    def test_clause_cites_its_source(self):
        for cause in SUPPORTED_CAUSE_TYPES:
            clause = legal_basis.procedure_clause(cause)
            assert "依据：" in clause


# ============================================================
# 4. 级别管辖跃迁：同一案由下客体不同，法院层级完全不同
# ============================================================

class TestJurisdictionUpgrade:
    def test_computer_software_upgrades_a_copyright_case(self):
        desc = "被告未经许可复制并销售原告享有著作权的计算机软件，反编译后获得源代码。"
        clause = legal_basis.procedure_clause(CAUSE_COPYRIGHT, desc)
        assert "知识产权法院" in clause
        assert "中级人民法院" in clause
        assert "计算机软件" in clause

    def test_trade_secret_upgrades_an_unfair_competition_case(self):
        desc = "被告通过前员工获取原告的技术秘密，违反保密义务披露给竞争对手。"
        clause = legal_basis.procedure_clause(CAUSE_UNFAIR_COMPETITION, desc)
        assert "知识产权法院" in clause
        assert "技术秘密" in clause

    def test_famous_trademark_upgrades_a_trademark_case(self):
        desc = "原告商标系驰名商标，被告在跨类商品上使用近似标识。"
        clause = legal_basis.procedure_clause(CAUSE_TRADEMARK, desc)
        assert "知识产权法院" in clause
        assert "驰名商标" in clause

    def test_ordinary_case_stays_at_the_basic_court(self):
        """没有特殊客体时，不该凭空把管辖抬高一级"""
        for cause, desc in (
            (CAUSE_COPYRIGHT, "被告在店铺主图使用与原告美术作品实质性相似的图案。"),
            (CAUSE_UNFAIR_COMPETITION, "被告使用与原告有一定影响的商品装潢近似的包装。"),
            (CAUSE_TRADEMARK, "被告在同类商品上使用与原告注册商标近似的标识。"),
        ):
            clause = legal_basis.procedure_clause(cause, desc)
            assert "知识产权法院" not in clause, f"{cause} 不该发生管辖跃迁：{clause}"
            assert "基层人民法院" in clause

    def test_upgrade_keywords_do_not_leak_across_causes(self):
        """
        商标案由的案情里提到"计算机软件"，不构成著作权的管辖跃迁事由——
        跃迁必须落在本案由自己的客体清单里。
        """
        clause = legal_basis.procedure_clause(
            CAUSE_TRADEMARK, "原告开发的计算机软件上使用其注册商标。")
        assert "知识产权法院" not in clause

    def test_empty_description_never_upgrades(self):
        for cause in SUPPORTED_CAUSE_TYPES:
            assert legal_basis.detect_level_upgrade(cause, "") is None
            assert legal_basis.detect_level_upgrade(cause, None) is None


# ============================================================
# 5. 判例价值基准
# ============================================================

class TestPrecedentClause:
    def test_focus_differs_per_cause(self):
        focuses = {
            legal_basis.precedent_clause(c) for c in SUPPORTED_CAUSE_TYPES}
        assert len(focuses) == 3

    def test_unfair_precedent_focuses_on_the_article_6_threshold(self):
        """「有一定影响」是不正当竞争混淆行为的前提性要件，判例价值也围绕它"""
        clause = legal_basis.precedent_clause(CAUSE_UNFAIR_COMPETITION)
        assert "有一定影响" in clause


# ============================================================
# 6. 节点 prompt 真的把基准带进去了
# ============================================================

class TestNodePrompts:
    """
    Mock 模式下这些节点直接返回 mock_*，prompt 一行都不会拼。
    所以这里必须单独抓一次真实拼装，否则"接了法律依据"这件事没有证据。
    """

    @staticmethod
    def _capture(monkeypatch):
        captured = {}

        def fake_call_json(system, prompt, node=None, **kwargs):
            captured["prompt"] = prompt
            return {"score": 80, "analysis": "ok"}

        monkeypatch.setattr(evaluate_nodes, "call_json", fake_call_json)
        return captured

    def test_damages_prompt_carries_the_statutory_range(self, monkeypatch):
        captured = self._capture(monkeypatch)
        evaluate_nodes.evaluate_damages(_ctx(cause_type=CAUSE_COPYRIGHT))
        assert "五百元以上五百万元以下" in captured["prompt"]

    def test_procedure_prompt_carries_the_limitation_rule(self, monkeypatch):
        captured = self._capture(monkeypatch)
        evaluate_nodes.evaluate_procedure(_ctx(cause_type=CAUSE_COPYRIGHT))
        assert "向前推算三年计算" in captured["prompt"]
        assert "常见抗辩" in captured["prompt"]

    def test_procedure_prompt_reflects_jurisdiction_upgrade(self, monkeypatch):
        captured = self._capture(monkeypatch)
        evaluate_nodes.evaluate_procedure(_ctx(
            cause_type=CAUSE_COPYRIGHT,
            case_description="被告复制并销售原告享有著作权的计算机软件。"))
        assert "知识产权法院" in captured["prompt"]

    def test_precedent_prompt_carries_the_cause_focus(self, monkeypatch):
        captured = self._capture(monkeypatch)
        evaluate_nodes.evaluate_precedent(_ctx(
            cause_type=CAUSE_UNFAIR_COMPETITION, goal_type="要名"))
        assert "有一定影响" in captured["prompt"]

    def test_unknown_cause_fails_loudly_in_the_node(self, monkeypatch):
        """未知案由必须抛错，不许拼出一个"看起来还行"的通用 prompt"""
        self._capture(monkeypatch)
        with pytest.raises(ValueError, match="不支持的案由"):
            evaluate_nodes.evaluate_damages(_ctx(cause_type="专利侵权"))

    def test_prompt_markers_used_by_the_real_path_test_survive(self, monkeypatch):
        """
        tests/test_l2_real_path.py 靠这五个子串分派假响应，认不出就直接抛错。
        改 prompt 措辞时若动了它们，那条链路的测试会失去意义——这里提前挡住。
        """
        markers = {
            "rights": "评估原告权利基础的稳固程度",
            "infringement": "逐一认定以下构成要件",
            "procedure": "评估诉讼程序可行性",
            "damages": "估算本案判赔规模",
            "precedent": "评估本案的判例价值",
        }
        cases = {
            "rights": (evaluate_nodes.evaluate_rights,
                       _ctx(cause_type=CAUSE_TRADEMARK)),
            "infringement": (evaluate_nodes.evaluate_infringement,
                             _ctx(cause_type=CAUSE_TRADEMARK)),
            "procedure": (evaluate_nodes.evaluate_procedure,
                          _ctx(cause_type=CAUSE_TRADEMARK)),
            "damages": (evaluate_nodes.evaluate_damages,
                        _ctx(cause_type=CAUSE_TRADEMARK)),
            "precedent": (evaluate_nodes.evaluate_precedent,
                          _ctx(cause_type=CAUSE_TRADEMARK, goal_type="要名")),
        }
        for node, (fn, ctx) in cases.items():
            captured = self._capture(monkeypatch)
            fn(ctx)
            assert markers[node] in captured["prompt"], (
                f"{node} 的 prompt 丢了特征词，test_l2_real_path 的分派会失效")
