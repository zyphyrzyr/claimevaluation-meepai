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
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import evaluate_nodes, scoring
from .case_context import CaseContext
from .config import get_runtime_settings
from .evidence_review import review_evidence
from .legal_rules import run_rule_engine, build_evidence_checklist, infer_uploaded_proof_categories
from .pkulaw import pkulaw_api

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


class StopRun(Exception):
    """暂停检查点检测到「终止」信号时抛出，由 run_all 的调用方干净收尾。"""


class PauseControl:
    """暂停/终止信号载体。

    - pause / abort 为 threading.Event，由外部端点置位；
    - pause_point() 在节点与步骤边界调用：终止即抛 StopRun，暂停则阻塞等待。
    """

    def __init__(self, pause=None, abort=None):
        self.pause = pause
        self.abort = abort

    def is_abort(self) -> bool:
        return bool(self.abort and self.abort.is_set())

    def is_pause(self) -> bool:
        return bool(self.pause and self.pause.is_set())

    def pause_point(self, on_event: EventCallback) -> None:
        """暂停/终止检查点。

        - 命中终止：抛 StopRun（调用方负责清场）。
        - 命中暂停：发 flow_paused 并阻塞，直到暂停被清除；恢复时发 flow_resumed。
        注意：flow_paused / flow_resumed 只在「状态切换」那一刻各发一次。
        """
        if self.is_abort():
            raise StopRun
        if self.is_pause():
            _emit(on_event, "flow_paused", "", label="评估已暂停", status="paused")
            while self.is_pause() and not self.is_abort():
                time.sleep(0.25)
            if self.is_abort():
                raise StopRun
            _emit(on_event, "flow_resumed", "", label="评估已恢复", status="running")


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
    def __init__(self, ctx: CaseContext, on_event: EventCallback = None,
                 pause_event=None, abort_event=None, progress_saver=None):
        self.ctx = ctx
        self.on_event = on_event
        self.mock = _use_mock()
        self.control = PauseControl(pause_event, abort_event)
        # progress_saver: 每产出一个维度结果后立即落库 context_json 的可选回调，
        # 让前端「节点完成即刷新详情」拿到实时数据（默认只在整轮 finally 落库）。
        self._progress_saver = progress_saver

    def _set_dim(self, node: str, result: Dict[str, Any], status: str = "ok",
                 error: str = None) -> None:
        """写维度结果，并即时落库进度（仅 context_json），供前端实时刷新。"""
        self.ctx.set_dimension(node, result, status=status, error=error)
        if self._progress_saver is not None:
            try:
                self._progress_saver()
            except Exception:
                pass

    def _pkulaw_session(self):
        """一次评估共用的类案检索预算账本（见 pkulaw/precedent_ladder）。

        多个节点都要类案，各自记账的话一次评估会跑掉二十几次 RPC，
        串行起来足以让演示超时——所以账本挂在编排器上，按整轮评估统一限额。
        """
        if getattr(self, "_ladder_session", None) is None:
            from .pkulaw.precedent_ladder import LadderSession
            self._ladder_session = LadderSession()
        return self._ladder_session

    def _pkulaw_hint(self):
        """汇总可用的地域线索，供类案检索的顺位③换算本省高级人民法院。

        省份来源优先级（用户 2026-09-19 定：被告工商所在地兜底）：
        工商登记「所属地区」> 被告 location_hint > 案情描述。管辖法院系统不采集，
        顺位④跳过并写明原因，不猜。
        """
        from .pkulaw.court_resolver import hint_from_case
        profile = getattr(self.ctx, "defendant_profile", None) or {}
        facts = (profile.get("metrics") or {}).get("facts") or {}
        entity = facts.get("entity") or {}
        return hint_from_case(
            region=entity.get("region") or "",
            location_hint=(self.ctx.defendant_info or {}).get("location_hint") or "",
            case_description=self.ctx.case_description or "",
        )

    def _retrieve_legal_pkulaw(self, node: str):
        """法律可行性三节点接入北大法宝：检索权利基础 / 侵权认定 / 诉讼程序相关法条与类案。

        不可用时（未配置 token、节点不属此类、或 mock）返回 None —— 不注入、不报错，
        评估照常进行；检索失败则返回一个带 error 的 dict，由调用方在 trace 显式露出。
        """
        if self.mock:
            return None
        if node not in ("rights", "infringement", "procedure"):
            return None
        if not pkulaw_api._pkulaw_configured():
            return None
        try:
            if node == "rights":
                return pkulaw_api.search_for_rights_foundation(self.ctx.cause_type)
            if node == "infringement":
                return pkulaw_api.search_for_infringement(
                    self.ctx.cause_type,
                    hint=self._pkulaw_hint(),
                    session=self._pkulaw_session())
            return pkulaw_api.search_for_procedure()
        except Exception as e:  # 检索异常不应让评估节点挂掉
            return {"status": "error", "error": str(e)[:200],
                    "laws": [], "cases": [],
                    "_summary": f"北大法宝调用异常：{e}"}

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
        self._set_dim("evidence_review",
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
        self._set_dim("red_gate", {"hits": hits, "blocked": blocked},
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
        # 法律可行性三节点接入北大法宝外部依据：先把检索结果显式推到 trace，
        # 让界面能直观看到「评判有外部依据」，再注入 prompt 供模型参照。
        pkulaw_payload = self._retrieve_legal_pkulaw(node)
        if pkulaw_payload is not None:
            fail = pkulaw_payload.get("status") == "error"
            _mcp(cb, node, "北大法宝",
                 pkulaw_payload.get("_summary") or "已检索外部法律依据作为评判参照",
                 status="error" if fail else "ok",
                 detail=str(pkulaw_payload.get("error") or "")[:200])
        _step(cb, node,
              (f"组装判断依据：案情 {len(ctx.case_description or '')} 字 · "
               f"证据完整度 {_fmt_score(ctx.evidence_completeness)}% · "
               f"参考材料 {len(ctx.injected_knowledge or [])} 条 · "
               f"用户观点 {len(ctx.user_viewpoints or [])} 条"))
        self.control.pause_point(cb)
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
        self.control.pause_point(cb)
        try:
            result = fn(self.ctx, use_mock=self.mock, pkulaw=pkulaw_payload)
            failed = "error" in result
            self._set_dim(node, result,
                          status="failed" if failed else "ok",
                          error=result.get("error"))
            _step(cb, node,
                  f"{label}结论已解析：得分 {_fmt_score(result.get('score'))}",
                  status="failed" if failed else "ok",
                  detail=(str(result.get("analysis") or result.get("error") or ""))[:400])
            self.control.pause_point(cb)
        except Exception as e:  # 失败态：不静默落默认分
            self._set_dim(node, {}, status="failed", error=str(e))
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
                self._set_dim("damages", damages,
                                  status="failed" if "error" in damages else "ok",
                                  error=damages.get("error"))
            except Exception as e:
                self._set_dim("damages", {}, status="failed", error=str(e))
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
            self._set_dim("recovery",
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
                self._set_dim("precedent", precedent,
                                  status="failed" if "error" in precedent else "ok",
                                  error=precedent.get("error"))
            except Exception as e:
                self._set_dim("precedent", {}, status="failed", error=str(e))

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
            self.control.pause_point(cb)
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

        self._set_dim(
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

        def score_of(node: str) -> Tuple[Optional[float], bool]:
            """返回 (分数, 是否过期)。

            - status=="ok"：正常分数；
            - status=="stale"：过期但仍持有上次算出的分数 → 参与聚合，但调用方须把结论标「参考」；
            - 其余（failed / 不存在 / 分数为空或 NaN）：无可用分数 → (None, False)，
              由 missing 分支 withholding（这正是重跑上游把下游标记失效的目的：不让陈旧分静默复用）。
            NaN 不是「零分」，是「没算出分」，normalize(nan) 会被静默夹成 0 并触发幂平均
            一票否决，把整案打到 0 分而界面无提示——在此拦下，走 missing 分支。
            """
            d = ctx.dimension_results.get(node, {})
            s = (d.get("result") or {}).get("score")
            if s is None or (isinstance(s, float) and math.isnan(s)):
                return None, False
            if d.get("status") == "ok":
                return s, False
            if d.get("status") == "stale":
                return s, True
            return None, False

        rights_s, rights_stale = score_of("rights")
        infr_s, infr_stale = score_of("infringement")
        proc_s, proc_stale = score_of("procedure")

        # 过期维度仍参与聚合（其旧分数依旧可读），但整体结论须标「参考」而非正常结论。
        # 仅 failed / 不存在（分数取不到）才计入 missing → withholding。
        stale_labels = [NODE_LABELS[n] for n, st in
                        [("rights", rights_stale), ("infringement", infr_stale),
                         ("procedure", proc_stale)]
                        if st]
        missing = [NODE_LABELS[n] for n, s in
                   [("rights", rights_s), ("infringement", infr_s), ("procedure", proc_s)]
                   if s is None]

        legal = None
        if rights_s is not None and infr_s is not None and proc_s is not None:
            legal = scoring.calculate_legal_feasibility(
                rights_s, infr_s, proc_s, ctx.correction_coeff)

        # 业务子维度的过期同样计入「参考」标注：先置 False，分支里再覆盖
        damages_stale = False
        precedent_stale = False
        if ctx.goal_type == "要钱":
            damages_s, damages_stale = score_of("damages")
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
            precedent_s, precedent_stale = score_of("precedent")
            business = None
            if precedent_s is not None:
                business = scoring.calculate_business_expectation(
                    "要名", precedent_value=precedent_s)
            else:
                missing.append(NODE_LABELS["business"] + "(判例价值)")

        # 业务子维度过期 → 一并计入「参考」标注（其旧分仍参与聚合，结论标参考而非消失）
        for n, st in (("damages", damages_stale), ("precedent", precedent_stale)):
            if st:
                stale_labels.append(NODE_LABELS[n])

        has_stale = bool(stale_labels)  # 过期分仍参与聚合 → is_complete 为真；仅决定结论标「参考」
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
            stale_dimensions=stale_labels,
            dimension_scores={
                "权利基础": rights_s, "侵权认定": infr_s, "诉讼程序": proc_s,
            } if not blocked else {},
            confidence=ctx.confidence,
        )
        synth_status = ("blocked" if blocked
                        else "partial" if has_stale
                        else "ok" if is_complete else "partial")
        self._set_dim("synthesize", {
            "scores": ctx.scores,
            "confidence": ctx.confidence,
            "recommendation": ctx.recommendation,
            "missing": missing,
            "stale_dimensions": stale_labels,
        }, status=synth_status)

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
              status=synth_status,
              detail=(f"置信度 {_fmt_score(ctx.confidence)}%"
                      + (f"；未产出维度：{'、'.join(missing)}" if missing else "")
                      + (f"；含过期维度（参考）：{'、'.join(stale_labels)}" if has_stale else "")))
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
        # 节点边界检查点：暂停则在此挂起（当前节点尚未开始），终止则抛出
        self.control.pause_point(self.on_event)
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

    def rerun_node(self, node: str, guidance: str = "", cascade: bool = True) -> CaseContext:
        """
        节点级重跑（§6.4）。

        cascade=True（默认，级联重跑）：重跑本节点后，按 DAG 拓扑顺序自动重算全部下游
            LLM 节点，最后规则环节（synthesize）瞬时重算。下游 LLM 重算失败 → 降级为
            stale：保留旧分数并标「参考」，绝不让整案结论被一票否决而消失。
        cascade=False（仅本节点）：下游 LLM 节点全部标记 stale（保留旧分数，聚合时按
            过期参与），仅 synthesize 瞬时重算。用于省成本或离线场景。

        引导意见始终注入 user_viewpoints，自动影响本节点及其下游。
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

        rerun_nodes: List[str] = []
        stale_nodes: List[str] = []
        label = NODE_LABELS.get(node, node)

        if cascade:
            for n in llm_nodes:
                prior = self.ctx.dimension_results.get(n)
                try:
                    self.run_node(n)
                except Exception:
                    # 重算异常：沿用旧结果并标 stale，不丢分、不隐藏
                    if prior is not None:
                        restored = dict(prior)
                        restored["status"] = "stale"
                        restored["stale_reason"] = f"上游「{label}」重跑后重算失败，沿用上次结果"
                        self.ctx.dimension_results[n] = restored
                    stale_nodes.append(n)
                    continue
                if self.ctx.dimension_results.get(n, {}).get("status") != "ok":
                    # 重算未成功（failed）：同上降级为 stale，保留旧分数
                    if prior is not None:
                        restored = dict(prior)
                        restored["status"] = "stale"
                        restored["stale_reason"] = f"上游「{label}」重跑后重算失败，沿用上次结果"
                        self.ctx.dimension_results[n] = restored
                    stale_nodes.append(n)
                else:
                    rerun_nodes.append(n)
            for n in rule_nodes:
                self.run_node(n)  # 纯规则，瞬时重算
        else:
            # 仅本节点：下游 LLM 标 stale（保留旧分数，聚合时按过期参与）
            self.ctx.mark_stale(llm_nodes, reason=f"上游「{label}」已重跑")
            stale_nodes = list(llm_nodes)
            for n in rule_nodes:
                self.run_node(n)

        self._last_rerun = {
            "node": node,
            "cascade": cascade,
            "rerun_nodes": rerun_nodes,
            "stale_nodes": stale_nodes,
        }

        if cascade:
            effect = (f"「{label}」已重跑并级联重算下游"
                      + (f"：{'、'.join(NODE_LABELS.get(n, n) for n in rerun_nodes)}" if rerun_nodes else "")
                      + ("；决策合成已重算" if rule_nodes else "")
                      + (f"；重算失败降级为参考：{'、'.join(NODE_LABELS.get(n, n) for n in stale_nodes)}"
                         if stale_nodes else ""))
        else:
            effect = (f"「{label}」已重跑（仅本节点）；决策合成已重算；"
                      + (f"下游待确认重跑：{'、'.join(NODE_LABELS.get(n, n) for n in stale_nodes)}"
                         if stale_nodes else "无下游待办"))
        self.ctx.log_event(
            "node_rerun", node=node,
            content=guidance,
            effect=effect,
        )
        return self.ctx
