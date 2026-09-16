"""
评估编排器（确定性状态机，§6.2 编排层）
流程：证据盘点 → 硬门禁 → 权利基础 → 侵权认定 → 诉讼程序 → 业务预期 → 决策合成
（模拟法庭在 P2 插入 诉讼程序 → 决策合成 之间，修正系数默认 1.0）

- 非自主 Agent：节点顺序确定，保证分数可复现、可审计
- 失败态：LLM 节点失败 → 该维度标记 failed，不静默落默认分
- 节点级重跑：rerun(node) → 下游按 DAG 标记 stale → 规则环节瞬时重算
"""

import math
import time
from typing import Any, Callable, Dict, List, Optional

from . import evaluate_nodes, scoring
from .case_context import CaseContext
from .config import get_runtime_settings
from .evidence_review import review_evidence
from .legal_rules import run_rule_engine, build_evidence_checklist, infer_uploaded_proof_categories

NODE_ORDER = [
    "evidence_review", "red_gate",
    "rights", "infringement", "procedure",
    "business", "synthesize",
]

NODE_LABELS = {
    "evidence_review": "证据盘点",
    "red_gate": "红线检查",
    "rights": "权利基础",
    "infringement": "侵权认定",
    "procedure": "诉讼程序",
    "business": "业务预期",
    "synthesize": "决策合成",
    # 业务预期的子维度：依赖图与审计文案需要它们的中文名
    # （它们不在 NODE_ORDER 里，但是真实写入 dimension_results 的键）
    "damages": "判赔规模",
    "recovery": "回款能力",
    "precedent": "判例价值",
}

# 下游依赖（重跑某节点时失效传播）
#
# 注意：键名必须与实际写入 dimension_results 的键一致，否则 mark_stale 会静默跳过。
# "business" 是流程节点名，_run_business() 并不写 business 键，它写的是子维度
# damages / recovery（要钱）或 precedent（要名）——依赖图必须指向这些真实键名。
# （早期实现指向不存在的 "business"，导致重跑证据盘点后判赔规模不会失效。评测报告 P1-2）
#
# 但 business 自己的下游只能是 synthesize：damages/recovery/precedent 是 business
# 的「产出」，不是它的「下游」。若把它们列为 business 的下游，重跑 business 会先把
# 三者算成 ok，紧接着又标成 stale——自相矛盾（修 P1-2 时一度引入）。
BUSINESS_DIMENSIONS = ["damages", "recovery", "precedent"]

# 允许用户在界面上手动重跑的节点（§6.4 节点级重跑）。
# 规则类节点不在此列：red_gate / synthesize 都是纯规则、零成本，
# 任何上游变动本来就会瞬时重算，让用户手动点一次没有意义。
RERUNNABLE_NODES = ["evidence_review", "rights", "infringement", "procedure", "business"]

DOWNSTREAM = {
    # 加上 "business" 汇总记录本身：子维度失效时它也应一起失效，
    # 否则会出现「子维度全部 stale、汇总却显示 ok」的自相矛盾展示
    "evidence_review": (["rights", "infringement", "procedure"]
                        + BUSINESS_DIMENSIONS + ["business", "synthesize"]),
    "red_gate": ["synthesize"],
    "rights": ["infringement", "synthesize"],
    "infringement": ["synthesize"],
    "procedure": ["synthesize"],
    "business": ["synthesize"],
    "synthesize": [],
}


def active_business_dimensions(goal_type: str) -> List[str]:
    """
    当前案件实际会走到的业务子维度。

    要钱 → damages/recovery；要名 → precedent。DOWNSTREAM 里三个都列了，但另一条
    分支的维度压根不会产生结果。若不去重，重跑证据盘点后 effect 文案会提示
    「待确认重跑：判例价值」——可要钱的案子根本没有这个节点，用户点进去找不到，
    顶部 stale 计数也虚高一项。
    """
    return ["damages", "recovery"] if goal_type == "要钱" else ["precedent"]


def active_downstream(node: str, goal_type: str) -> List[str]:
    """按案件实际路径剪掉不适用分支后的下游清单"""
    deps = DOWNSTREAM.get(node, [])
    if node != "evidence_review":
        return list(deps)
    active = set(active_business_dimensions(goal_type))
    return [n for n in deps if n not in BUSINESS_DIMENSIONS or n in active]

EventCallback = Optional[Callable[[Dict[str, Any]], None]]


def _emit(cb: EventCallback, event: str, node: str, **extra) -> None:
    if cb:
        cb({"event": event, "node": node, "label": NODE_LABELS.get(node, node), **extra})


def _step(cb: EventCallback, node: str, text: str, *,
          status: str = "ok", detail: str = "") -> None:
    """
    节点内部步骤事件。

    为什么需要它：原先只有 node_started / node_finished 两种粒度，界面上一个节点
    从开始到结束只能显示「计算中…」——用户看不到任何「在做什么」。steps 让「正在
    组装上下文 / 正在调用模型 / 正在核对规则」这些过程可被实时呈现。
    text 是给人看的一句话自然语言，不出现内部术语。
    """
    _emit(cb, "node_step", node, text=text, status=status, detail=detail)


def _mcp(cb: EventCallback, node: str, vendor: str, text: str, *,
         status: str = "ok", detail: str = "") -> None:
    """外部数据源调用事件（企查查 / 北大法宝），前端用厂商标签区分展示。"""
    _emit(cb, "mcp_call", node, vendor=vendor, text=text,
          status=status, detail=detail)


# 企查查 8 阶段 → 人话。stage key 与 qcc_api 写入的键一一对应。
_QCC_STAGE_TEXT = [
    ("A_主体锁定", "锁定被告主体（模糊搜索后按行业与所在地消歧）"),
    ("B_基本盘", "核对工商登记、财务与上市信息"),
    ("C_风险分诊", "对 35 类风险维度做整体分诊"),
    ("D_风险下钻", "下钻核查司法与经营风险明细"),
    ("E_人员风险", "核查实际控制人与高管的个人风险"),
    ("F_经营规模", "盘点商标资产与线上线下经营渠道"),
    ("G_诉讼时间", "统计被诉历史，估算诉讼周期"),
]


def _qcc_stage_detail(key: str, data: Dict[str, Any]) -> str:
    """把企查查某一阶段的结果压成一句话（形状不稳，全程防御式取值）。"""
    try:
        if not isinstance(data, dict):
            return ""
        if key == "A_主体锁定":
            return f"锁定主体：{data.get('locked_name') or '—'}"
        if key == "B_基本盘":
            parts = []
            for label in ("工商登记", "财务数据", "上市信息"):
                c = (data.get(label) or {}).get("_count", 0)
                if c:
                    parts.append(f"{label} {c} 条")
            return " · ".join(parts)
        if key == "C_风险分诊":
            return str(data.get("_summary") or "")
        if key == "D_风险下钻":
            hits = {k: v for k, v in data.items()
                    if isinstance(v, dict) and (v.get("_count") or 0) > 0}
            total = sum((v.get("_count") or 0) for v in hits.values())
            if not hits:
                return "未发现司法与经营风险记录"
            names = "、".join(list(hits.keys())[:4])
            return f"命中 {len(hits)} 类风险、共 {total} 条（{names}）"
        if key == "E_人员风险":
            people = [k for k, v in data.items() if isinstance(v, dict)]
            if not people:
                return "未获取到关键人员信息"
            hits = sum(
                (sub or {}).get("_count", 0)
                for v in data.values() if isinstance(v, dict)
                for sub in (v.get("scans") or {}).values()
                if isinstance(sub, dict)
            )
            return f"核查 {len(people)} 位关键人员，命中风险记录 {hits} 条"
        if key == "F_经营规模":
            return str(data.get("_summary") or "")
        if key == "G_诉讼时间":
            c = (data.get("被诉历史") or {}).get("_count", 0)
            return f"被诉历史 {c} 件"
    except Exception:
        return ""
    return ""


def _fmt_score(v) -> str:
    """分数格式化：整数不显示小数尾巴（78.0 → 78）。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _use_mock() -> bool:
    return get_runtime_settings()["use_mock"]


class Orchestrator:
    def __init__(self, ctx: CaseContext, on_event: EventCallback = None):
        self.ctx = ctx
        self.on_event = on_event
        self.mock = _use_mock()

    # -------------------------------------------------- 各节点执行

    def _run_evidence_review(self) -> None:
        ctx = self.ctx
        cb = self.on_event
        evidence_texts = ctx.defendant_info.get("evidence_texts", "")
        _step(cb, "evidence_review",
              "读取随案材料与案情描述，逐项核对权利证明与侵权固定证据")
        result = review_evidence(
            ctx.case_description, ctx.cause_type,
            evidence_texts=evidence_texts,
            user_viewpoints=ctx.viewpoints_text(),
            use_mock=self.mock,
        )
        ctx.evidence_matrix = result["matrix"]
        ctx.gap_list = result["gap_list"]
        ctx.extra_evidence = result["extra_evidence"]
        ctx.evidence_completeness = result["completeness"]
        ctx.evidence_note = result.get("note", "")
        ctx.set_dimension("evidence_review",
                          {"completeness": result["completeness"],
                           "gap_count": len(result["gap_list"])},
                          status="failed" if result.get("error") else "ok",
                          error=result.get("error"))
        gaps = result["gap_list"] or []
        _step(cb, "evidence_review",
              f"核对完成：证据完整度 {result['completeness']}%，发现 {len(gaps)} 项缺口",
              status="failed" if result.get("error") else "ok",
              detail="；".join(
                  f"[{g.get('category', '')}] {g.get('item', '')}（{g.get('status', '')}：{g.get('reason', '')}）"
                  for g in gaps[:5]
              ))

    def _run_red_gate(self) -> None:
        ctx = self.ctx
        cb = self.on_event
        _step(cb, "red_gate",
              "装载 6 条程序性规则（诉讼时效、主体资格、仲裁条款、权利证明、侵权证据、损失证据）")
        # mock 与真实模式走同一条规则引擎代码路径。
        # 早期实现在 mock 下直接返回 mock_rule_hits()（6 条全 pass/warning），
        # 真实模式的字段缺失问题因此被完全掩盖（评测报告 P0-1）。
        # 规则引擎读的是 parties / evidence_checklist，而不是 v4 的 evidence_matrix，
        # 两者之间必须经 build_evidence_checklist 映射。
        # evidence_upload：把「已上传但系统未能读取」的证据从「证据缺失」里剥出来，
        # 避免解析失败（如缺 OCR 引擎）被误判为客户证据缺失而 block（2026-09-16）。
        case_facts = {
            "timeline": ctx.defendant_info.get("timeline", []),
            "case_description": ctx.case_description,
            "parties": ctx.parties,
            "evidence_checklist": build_evidence_checklist(ctx.evidence_matrix),
            "evidence_upload": infer_uploaded_proof_categories(ctx.evidence_files_meta),
        }
        hits = run_rule_engine(case_facts)
        ctx.red_flags = hits
        blocked = scoring.has_block_red_flag(hits)
        ctx.set_dimension("red_gate", {"hits": hits, "blocked": blocked},
                          status="blocked" if blocked else "ok")
        sev_cn = {"pass": "通过", "warning": "警示", "block": "拦截"}
        counts = {"pass": 0, "warning": 0, "block": 0}
        for h in hits:
            counts[h.get("severity", "pass")] = counts.get(h.get("severity", "pass"), 0) + 1
        _step(cb, "red_gate",
              (f"逐条判定完成：{counts['pass']} 条通过、{counts['warning']} 条警示、"
               f"{counts['block']} 条拦截"
               + ("，已命中红线，后续节点不再执行" if blocked else "，未触发拦截")),
              status="blocked" if blocked else "ok",
              detail="；".join(
                  f"{h.get('rule_name', '')}：{sev_cn.get(h.get('severity', ''), h.get('severity', ''))}"
                  f"（{h.get('reason', '')}）"
                  for h in hits
              ))

    def _run_llm_node(self, node: str) -> None:
        fn = {
            "rights": evaluate_nodes.evaluate_rights,
            "infringement": evaluate_nodes.evaluate_infringement,
            "procedure": evaluate_nodes.evaluate_procedure,
        }[node]
        ctx = self.ctx
        cb = self.on_event
        label = NODE_LABELS.get(node, node)
        _step(cb, node,
              (f"组装判断依据：案情 {len(ctx.case_description or '')} 字 · "
               f"证据完整度 {_fmt_score(ctx.evidence_completeness)}% · "
               f"参考材料 {len(ctx.injected_knowledge or [])} 条 · "
               f"用户观点 {len(ctx.user_viewpoints or [])} 条"))
        if self.mock:
            _step(cb, node, f"生成{label}判断（当前为模拟数据，未调用模型）")
        else:
            try:
                from .llm_gateway import pick_model
                model = pick_model(node)
            except Exception:
                model = "—"
            _step(cb, node,
                  f"调用模型 {model} 做{label}分析，要求返回结论、优势与风险清单")
        try:
            result = fn(self.ctx, use_mock=self.mock)
            failed = "error" in result
            self.ctx.set_dimension(node, result,
                                   status="failed" if failed else "ok",
                                   error=result.get("error"))
            _step(cb, node,
                  f"{label}结论已解析：得分 {_fmt_score(result.get('score'))}",
                  status="failed" if failed else "ok",
                  detail=(str(result.get("analysis") or result.get("error") or ""))[:400])
        except Exception as e:  # 失败态：不静默落默认分
            self.ctx.set_dimension(node, {}, status="failed", error=str(e))
            _step(cb, node, f"{label}分析失败：{e}", status="failed")

    def _run_business(self) -> None:
        ctx = self.ctx
        cb = self.on_event
        if ctx.goal_type == "要钱":
            # 判赔规模（LLM）× 回款能力（企查查规则，零 LLM）
            _step(cb, "business", "第一步：评估判赔规模（结合同类案件判赔区间）")
            damages: Dict[str, Any] = {}
            try:
                damages = evaluate_nodes.evaluate_damages(ctx, use_mock=self.mock)
                ctx.set_dimension("damages", damages,
                                  status="failed" if "error" in damages else "ok",
                                  error=damages.get("error"))
            except Exception as e:
                ctx.set_dimension("damages", {}, status="failed", error=str(e))
            _step(cb, "business",
                  (f"判赔规模完成：{_fmt_score(damages.get('score'))} 分"
                   + (f"，参考区间 P10 {_fmt_score(damages.get('p10'))} 万 / "
                      f"P50 {_fmt_score(damages.get('p50'))} 万 / "
                      f"P90 {_fmt_score(damages.get('p90'))} 万"
                      if damages.get("p50") is not None else "")),
                  status="failed" if damages.get("error") else "ok",
                  detail=str(damages.get("analysis") or damages.get("error") or "")[:400])

            if self.mock:
                from .mock import mock_defendant_profile
                profile = mock_defendant_profile()
                _mcp(cb, "business", "企查查", "查询被告的工商、经营与风险状况（当前为模拟数据）",
                     status="warning", detail="USE_MOCK=True，未发起真实外部调用")
            else:
                _step(cb, "business", "第二步：向企查查查询被告的经营与风险状况（共 8 个环节）")
                from .qcc_api import search_for_financial_qcc_full
                try:
                    profile = search_for_financial_qcc_full(ctx.defendant_info)
                except Exception as e:
                    profile = {"error": str(e)}
                self._emit_qcc_stages(profile)
            ctx.defendant_profile = profile
            metrics = profile.get("metrics", {}) if isinstance(profile, dict) else {}
            ctx.recovery_ability = metrics.get("recovery_probability")
            ctx.set_dimension("recovery",
                              {"recovery_ability": ctx.recovery_ability,
                               "red_flags": metrics.get("red_flags", []),
                               "green_flags": metrics.get("green_flags", [])},
                              status="failed" if profile.get("error") else "ok",
                              error=profile.get("error"))
            _step(cb, "business",
                  (f"第三步：按规则计算回款能力：{_fmt_score(ctx.recovery_ability)} 分"
                   + (f"，判赔调整「{metrics.get('damages_adjustment')}」"
                      if metrics.get("damages_adjustment") else "")
                   + (f"，预计诉讼周期延长 {metrics.get('time_extra_months')} 个月"
                      if metrics.get("time_extra_months") else "")),
                  status="failed" if profile.get("error") else "ok",
                  detail="；".join(
                      [f"风险信号：{f}" for f in (metrics.get("red_flags") or [])[:3]]
                      + [f"利好信号：{f}" for f in (metrics.get("green_flags") or [])[:3]]
                  ))
        else:
            _step(cb, "business", "按判例价值维度评估业务预期（检索同类在先判决）")
            try:
                precedent = evaluate_nodes.evaluate_precedent(ctx, use_mock=self.mock)
                ctx.set_dimension("precedent", precedent,
                                  status="failed" if "error" in precedent else "ok",
                                  error=precedent.get("error"))
            except Exception as e:
                ctx.set_dimension("precedent", {}, status="failed", error=str(e))

        self._record_business_rollup()

    def _emit_qcc_stages(self, profile: Dict[str, Any]) -> None:
        """
        把企查查 8 阶段逐步推给前端。

        为什么按「阶段」而不是按「工具」：一次画像约 70+ 次 RPC，逐工具推会刷屏；
        阶段粒度（8 条）既能说明「正在查什么」，也与界面上一屏能承载的量匹配。
        """
        cb = self.on_event
        if not isinstance(profile, dict):
            return
        if profile.get("error"):
            _mcp(cb, "business", "企查查", "查询未成功，外部分析已跳过",
                 status="warning", detail=str(profile.get("error"))[:200])
            return
        stages = profile.get("stages") or {}
        for key, sentence in _QCC_STAGE_TEXT:
            data = stages.get(key)
            if data is None:
                continue
            _mcp(cb, "business", "企查查", sentence,
                 detail=_qcc_stage_detail(key, data))
        _step(cb, "business",
              "企查查返回结果已并入回款能力计算（本地规则，不消耗模型调用）")

    def _record_business_rollup(self) -> None:
        """
        汇总一条 business 节点自身的 dimension 记录。

        此前 _run_business() 只写 damages/recovery/precedent 三个子维度，
        business 键根本不存在 → run_node 读状态永远得到默认值 "ok"，
        失败的业务预期在流程进度里显示为成功，前端也拿不到「走的哪条路径」。
        """
        ctx = self.ctx
        sub_dims = active_business_dimensions(ctx.goal_type)
        statuses = [ctx.dimension_results.get(d, {}).get("status") for d in sub_dims]

        if any(s == "failed" for s in statuses):
            status, error = "failed", "业务预期子维度存在失败"
        elif any(s is None for s in statuses):
            status, error = "failed", "业务预期子维度未产出结果"
        else:
            status, error = "ok", None

        ctx.set_dimension(
            "business",
            {"goal_type": ctx.goal_type,
             "sub_dimensions": sub_dims,
             "scores": {d: (ctx.dimension_results.get(d, {}).get("result") or {}).get("score")
                        for d in sub_dims}},
            status=status, error=error)

    def _run_synthesize(self) -> None:
        ctx = self.ctx
        cb = self.on_event
        _step(cb, "synthesize", "读取权利基础、侵权认定、诉讼程序三个维度的得分")

        def score_of(node: str) -> Optional[float]:
            d = ctx.dimension_results.get(node, {})
            if d.get("status") != "ok":
                return None
            s = (d.get("result") or {}).get("score")
            # NaN 不是「零分」，是「没算出分」。
            # 若直接放进评分，normalize(nan) 会被静默夹成 0，幂平均的一票否决
            # 再把整案打到 0 分、结论落到「暂缓」——界面上却看不到任何异常提示。
            # 在这里拦下来，走 missing 分支，让报告明确写出「该维度未产出分数」。
            if s is None or (isinstance(s, float) and math.isnan(s)):
                return None
            return s

        rights_s = score_of("rights")
        infr_s = score_of("infringement")
        proc_s = score_of("procedure")

        missing = [NODE_LABELS[n] for n, s in
                   [("rights", rights_s), ("infringement", infr_s), ("procedure", proc_s)]
                   if s is None]

        legal = None
        if rights_s is not None and infr_s is not None and proc_s is not None:
            legal = scoring.calculate_legal_feasibility(
                rights_s, infr_s, proc_s, ctx.correction_coeff)

        if ctx.goal_type == "要钱":
            damages_s = score_of("damages")
            recovery_s = ctx.recovery_ability
            business = None
            if damages_s is not None and recovery_s is not None:
                business = scoring.calculate_business_expectation(
                    "要钱", damages_scale=damages_s, recovery_ability=recovery_s)
            if damages_s is None:
                missing.append(NODE_LABELS["business"] + "(判赔规模)")
            if recovery_s is None:
                missing.append(NODE_LABELS["business"] + "(回款能力)")
        else:
            precedent_s = score_of("precedent")
            business = None
            if precedent_s is not None:
                business = scoring.calculate_business_expectation(
                    "要名", precedent_value=precedent_s)
            else:
                missing.append(NODE_LABELS["business"] + "(判例价值)")

        final = None
        if legal is not None and business is not None:
            final = scoring.calculate_overall_score(legal, business)

        blocked = scoring.has_block_red_flag(ctx.red_flags)
        ctx.confidence = scoring.calculate_confidence(
            ctx.evidence_completeness,
            retrieval_complete=(ctx.dimension_results.get("recovery", {}).get("status") != "failed"),
        )
        ctx.scores = {
            "legal_feasibility": legal,
            "business_expectation": business,
            "final": final,
        }
        is_complete = final is not None and not missing
        ctx.recommendation = scoring.generate_recommendation(
            final, ctx.red_flags,
            is_complete=is_complete, missing_dimensions=missing,
            dimension_scores={
                "权利基础": rights_s, "侵权认定": infr_s, "诉讼程序": proc_s,
            } if not blocked else {},
            confidence=ctx.confidence,
        )
        ctx.set_dimension("synthesize", {
            "scores": ctx.scores,
            "confidence": ctx.confidence,
            "recommendation": ctx.recommendation,
            "missing": missing,
        }, status="blocked" if blocked else ("ok" if is_complete else "partial"))

        coeff = ctx.correction_coeff
        _step(cb, "synthesize",
              ("已应用对抗检验修正系数：法律可行性按模拟法庭结论折算"
               if coeff is not None and coeff != 1
               else "对抗压力测试尚未进行，法律可行性按「未经修正」计入"),
              detail=(f"当前修正系数 {_fmt_score(coeff)}"
                      if coeff is not None and coeff != 1 else
                      "跑一次模拟法庭压力测试，可以看到对方抗辩会把结论拉低多少"))
        _step(cb, "synthesize",
              (f"法律可行性 {_fmt_score(ctx.scores.get('legal_feasibility'))} 分与 "
               f"业务预期 {_fmt_score(ctx.scores.get('business_expectation'))} 分"
               f"共同决定主诉决策分 {_fmt_score(ctx.scores.get('final'))} 分"),
              status="blocked" if blocked else ("ok" if is_complete else "partial"),
              detail=(f"置信度 {_fmt_score(ctx.confidence)}%"
                      + (f"；未产出维度：{'、'.join(missing)}" if missing else "")))
        _step(cb, "synthesize",
              f"最终建议：{ctx.recommendation.get('recommendation', '—')}",
              status="blocked" if blocked else "ok",
              detail=str(ctx.recommendation.get("reason", ""))[:400])

        # 硬门禁命中：流程终止信号
        if blocked:
            ctx.log_event("red_gate_block", node="red_gate",
                          effect="命中程序性红线，输出暂不建议起诉，流程终止")

    # -------------------------------------------------- 流程控制

    def run_all(self) -> CaseContext:
        """完整跑一遍主流程；硬门禁命中即终止"""
        for node in NODE_ORDER:
            self.run_node(node)
            if node == "red_gate" and scoring.has_block_red_flag(self.ctx.red_flags):
                # 红线命中：直接合成"暂不建议起诉"，后续节点不再调用
                self.run_node("synthesize")
                _emit(self.on_event, "flow_blocked", "red_gate")
                break
        return self.ctx

    def run_node(self, node: str) -> None:
        started = time.time()
        _emit(self.on_event, "node_started", node)
        if node == "evidence_review":
            self._run_evidence_review()
        elif node == "red_gate":
            self._run_red_gate()
        elif node in ("rights", "infringement", "procedure"):
            self._run_llm_node(node)
        elif node == "business":
            self._run_business()
        elif node == "synthesize":
            self._run_synthesize()
        status = self.ctx.dimension_results.get(node, {}).get("status", "ok")
        _emit(self.on_event, "node_finished", node, status=status,
              duration_ms=int((time.time() - started) * 1000),
              summary=self._node_summary(node))

    def _node_summary(self, node: str) -> str:
        """
        节点完成后给人看的一句话结论（时间线上那一行）。

        刻意不复用报告文案：报告面向「结论+论证」，时间线面向「刚发生了什么」，
        所以这里要说清「查了什么、得出什么」，而不是复述分数。
        """
        ctx = self.ctx
        try:
            if node == "evidence_review":
                return (f"核对随案材料完成：证据完整度 {_fmt_score(ctx.evidence_completeness)}%，"
                        f"发现 {len(ctx.gap_list or [])} 项缺口。")
            if node == "red_gate":
                hits = ctx.red_flags or []
                blocks = [h for h in hits if h.get("severity") == "block"]
                if blocks:
                    return ("程序性规则检查命中红线：" +
                            "；".join(h.get("rule_name", "") for h in blocks) +
                            "——按规则暂不建议起诉，后续维度不再评估。")
                warns = [h for h in hits if h.get("severity") == "warning"]
                return (f"{len(hits)} 条程序性规则逐条判定，"
                        + (f"{len(warns)} 条给出警示但未拦截。" if warns else "全部通过，未触发拦截。"))
            if node in ("rights", "infringement", "procedure"):
                r = (ctx.dimension_results.get(node, {}).get("result") or {})
                if r.get("error"):
                    return f"{NODE_LABELS.get(node, node)}未产出结论：{r['error']}"
                analysis = str(r.get("analysis") or "").strip()
                head = f"{NODE_LABELS.get(node, node)}评分 {_fmt_score(r.get('score'))} 分。"
                return head + (analysis[:120] + ("…" if len(analysis) > 120 else "") if analysis else "")
            if node == "business":
                if ctx.goal_type == "要钱":
                    d = (ctx.dimension_results.get("damages", {}).get("result") or {})
                    return (f"判赔规模 {_fmt_score(d.get('score'))} 分，"
                            f"回款能力 {_fmt_score(ctx.recovery_ability)} 分"
                            + ("；未取到被告画像，回款能力无法评估。"
                               if ctx.recovery_ability is None else "。"))
                p = (ctx.dimension_results.get("precedent", {}).get("result") or {})
                return f"判例价值 {_fmt_score(p.get('score'))} 分。"
            if node == "synthesize":
                if ctx.recommendation.get("level") == "block":
                    return f"综合结论：{ctx.recommendation.get('recommendation', '暂不建议起诉')}。"
                return (f"主诉决策分 {_fmt_score(ctx.scores.get('final'))} 分，"
                        f"建议：{ctx.recommendation.get('recommendation', '—')}。")
        except Exception:
            return ""
        return ""

    def rerun_node(self, node: str, guidance: str = "") -> CaseContext:
        """
        节点级重跑（§6.4）：
        1. 引导意见注入 user_viewpoints（自动影响后续所有节点）
        2. 重跑该节点
        3. 下游按 DAG 标记 stale；规则环节（synthesize）瞬时重算，LLM 环节标记待确认
        """
        if node not in NODE_ORDER:
            raise ValueError(f"未知节点: {node}")
        if guidance:
            self.ctx.add_viewpoint(guidance, source=f"节点重跑引导({NODE_LABELS.get(node, node)})")

        self.run_node(node)
        # 按案件实际路径剪枝：要钱的案子不该提示去重跑「判例价值」
        downstream = active_downstream(node, self.ctx.goal_type)
        rule_nodes = [n for n in downstream if n == "synthesize"]
        llm_nodes = [n for n in downstream if n != "synthesize"]

        self.ctx.mark_stale(llm_nodes, reason=f"上游「{NODE_LABELS.get(node, node)}」已重跑")
        for n in rule_nodes:
            self.run_node(n)  # 纯规则，瞬时重算

        self.ctx.log_event(
            "node_rerun", node=node,
            content=guidance,
            effect=f"「{NODE_LABELS.get(node, node)}」已重跑；决策合成已重算；"
                   + (f"待确认重跑：{'、'.join(NODE_LABELS.get(n, n) for n in llm_nodes)}"
                      if llm_nodes else "无下游待办"),
        )
        return self.ctx
