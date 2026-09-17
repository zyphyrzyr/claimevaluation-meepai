"""
L0 评估三态（暂停 / 恢复 / 终止）验证
运行：backend/ 目录 pytest tests/test_l0_pause_resume_stop.py -v

背景：评估主流程原先一旦启动就无法中途干预。现在在 orchestrator 的检查点
（节点边界 / LLM 步骤边界 / 企查查阶段边界）注入 PauseControl，实现三态控制。
本文件验证这套控制本身的语义，不含界面层（界面由 SSR 冒烟另测）。

为什么这些用例不会 flaky：
凡是涉及线程的用例都**先把信号置好、再启动线程**，所以首个检查点必然命中，
完全不依赖 mock 评估的自然耗时。反过来若「启动后置信号」，mock 评估几十毫秒
就跑完了，「到底挂着还是已经跑完」会变成随机结果。
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import orchestrator
from core.case_context import CaseContext


# ============================================================
# helpers
# ============================================================

def _good_ctx(**overrides) -> CaseContext:
    """证据齐备的标准案件"""
    kwargs = dict(
        case_id="pauseresume-1",
        case_description="原告持有第1234567号注册商标（第25类服装），被告在天猫店铺销售近似标识卫衣，已公证取证。",
        cause_type="商标侵权",
        goal_type="要钱",
    )
    kwargs.update(overrides)
    return CaseContext(**kwargs)


SUFFICIENT_MATRIX = [
    {"id": "权利基础证据-1", "category": "权利基础证据", "item": "商标注册证", "status": "sufficient"},
    {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "侵权截图", "status": "sufficient"},
    {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "销量证据", "status": "sufficient"},
]


def _wait_for(events, name, timeout=10.0) -> bool:
    """轮询等待某个 SSE 事件出现"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e.get("event") == name for e in events):
            return True
        time.sleep(0.02)
    return False


def _start_run(ctx, pause, abort, events):
    """在后台线程跑 run_all，返回 (thread, outcome)。

    outcome 是主线程与工作线程之间唯一的结果通道：
      {"done": True}  正常跑完
      {"stopped": True} 抛出 StopRun（被「终止」干净打断）
      {"error": ...}  其它异常
    """
    orch = orchestrator.Orchestrator(ctx, on_event=events.append,
                                     pause_event=pause, abort_event=abort)
    outcome = {}

    def worker():
        try:
            orch.run_all()
            outcome["done"] = True
        except orchestrator.StopRun:
            outcome["stopped"] = True
        except Exception as exc:  # pragma: no cover - 仅用于暴露非预期异常
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t, outcome


# ============================================================
# 1. 暂停 → 真的挂起；恢复 → 真的继续
# ============================================================

class TestPauseAndResume:

    def test_pause_blocks_at_checkpoint_and_resume_finishes(self, monkeypatch):
        """先在起点挂起，再恢复，评估应接着跑完而不是重头再来"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        events = []
        pause, abort = threading.Event(), threading.Event()
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        pause.set()  # 先置位：线程一进入首个检查点就必然挂起
        t, outcome = _start_run(ctx, pause, abort, events)

        assert _wait_for(events, "flow_paused", 10), "未在检查点挂起并发出 flow_paused"

        # 挂着期间不能有任何推进：等一小会儿，仍应停在原地
        time.sleep(0.6)
        assert not outcome, f"暂停期间不应推进或结束，实际：{outcome}"
        assert ctx.dimension_results == {}, "首个检查点位于 run_node 开头，不应已产出任何维度结果"
        assert t.is_alive(), "线程应仍挂起在暂停检查点上（而不是已经跑完）"

        # 恢复
        pause.clear()
        t.join(timeout=60)
        assert not t.is_alive(), "恢复后应在超时前跑完"
        assert outcome.get("done") is True, f"恢复后应完整跑完，实际：{outcome}"
        assert any(e.get("event") == "flow_resumed" for e in events), "恢复后应发出 flow_resumed"
        assert len(ctx.dimension_results) >= 2, "恢复后应继续产出维度结果"

    def test_pause_checkpoint_is_hit_without_starving_the_flow(self, monkeypatch):
        """对照：不置任何信号，同样一条流程应能正常跑完（证明暂停逻辑没把主流程堵死）"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        events = []
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        t, outcome = _start_run(ctx, threading.Event(), threading.Event(), events)
        t.join(timeout=60)

        assert not t.is_alive(), "无控制信号的常规运行应正常结束"
        assert outcome.get("done") is True, f"常规运行不应被打断：{outcome}"
        assert not any(e.get("event") in ("flow_paused", "flow_resumed")
                       for e in events), "未下发控制信号时不应出现暂停/恢复事件"


# ============================================================
# 2. 终止 → 干净抛出 StopRun
# ============================================================

class TestAbort:

    def test_abort_before_start_raises_stoprun_at_first_checkpoint(self, monkeypatch):
        """终止信号在起点就生效：抛 StopRun 而不是把异常吞掉变成普通失败"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        events = []
        abort = threading.Event()
        abort.set()
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        orch = orchestrator.Orchestrator(ctx, on_event=events.append, abort_event=abort)

        with pytest.raises(orchestrator.StopRun):
            orch.run_all()

        assert ctx.dimension_results == {}, "终止应发生在任何节点产出之前"
        assert not any(e.get("event") == "flow_paused" for e in events), "未暂停过就不应出现暂停事件"

    def test_abort_while_paused_exits_the_wait_loop(self, monkeypatch):
        """挂起中收到终止：等待循环必须退出并抛 StopRun，不能永远睡在那个循环里"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        events = []
        pause, abort = threading.Event(), threading.Event()
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        pause.set()
        t, outcome = _start_run(ctx, pause, abort, events)
        assert _wait_for(events, "flow_paused", 10), "未进入暂停态"

        abort.set()  # 只置 abort、不清 pause：专门验证等待循环能被 abort 唤醒
        t.join(timeout=20)

        assert not t.is_alive(), "挂起中收到终止后线程应退出，不能一直挂着"
        assert outcome.get("stopped") is True, f"应以 StopRun 收尾，实际：{outcome}"
        assert not any(e.get("event") == "flow_resumed" for e in events), "被终止就不应再报「已恢复」"

    def test_stoprun_is_not_swallowed_by_node_error_handling(self, monkeypatch):
        """StopRun 必须穿透到 run_all 之上，不能被 run_node/_run_llm_node 的异常处理吞掉"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        events = []
        abort = threading.Event()
        abort.set()
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)
        orch = orchestrator.Orchestrator(ctx, on_event=events.append, abort_event=abort)

        # 直接走单个节点：每个节点入口都有检查点，都应干净抛出
        with pytest.raises(orchestrator.StopRun):
            orch.run_node("evidence_review")
        with pytest.raises(orchestrator.StopRun):
            orch.run_node("rights")

        assert ctx.dimension_results == {}, "任何节点都不应在终止信号已置位时产出结果"


# ============================================================
# 3. 终止 = 全部作废（reset_evaluation）
# ============================================================

class TestResetEvaluation:

    def test_wipes_decision_layer_only(self):
        """决策层全部清空，输入性字段不受影响——重跑时能从头重算而不是带着残留"""

        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)
        ctx.set_dimension("rights", {"score": 80}, status="ok")
        ctx.set_dimension("red_gate", {"blocked": False}, status="ok")
        ctx.scores = {"final": 70.0}
        ctx.confidence = 60.0
        ctx.red_flags = [{"rule_code": "R1", "severity": "pass"}]
        ctx.recommendation = {"recommendation": "建议优先启动"}
        ctx.defendant_profile = {"metrics": {"tm_count": 12}}
        ctx.recovery_ability = 0.5

        ctx.reset_evaluation()

        # 决策层：清空
        assert ctx.dimension_results == {}, "维度结果应清空"
        assert ctx.scores == {}, "分数应清空"
        assert ctx.confidence is None, "置信度应清空"
        assert ctx.red_flags == [], "红线应清空"
        assert ctx.recommendation == {}, "建议应清空"
        assert ctx.defendant_profile == {}, "被告画像应清空"
        assert ctx.recovery_ability is None, "回款能力应清空"

        # 输入层：保留（这些不是本次评估的产出，重跑也不该被抹掉）
        assert ctx.evidence_matrix == SUFFICIENT_MATRIX, "证据矩阵属输入，不应被清掉"
        assert ctx.case_description, "案情描述属输入，不应被清掉"
        assert ctx.case_id == "pauseresume-1"

    def test_reset_makes_rerun_start_from_scratch(self, monkeypatch):
        """清空后重跑：应能完整跑通，不会因残留而报错或跳过节点"""
        monkeypatch.setattr(orchestrator, "_use_mock", lambda: True)
        ctx = _good_ctx(evidence_matrix=SUFFICIENT_MATRIX)

        # 先跑一次，制造真实残留
        orchestrator.Orchestrator(ctx).run_all()
        assert ctx.dimension_results, "前置：首次运行应产出结果"

        ctx.reset_evaluation()
        assert ctx.dimension_results == {}

        events = []
        t, outcome = _start_run(ctx, threading.Event(), threading.Event(), events)
        t.join(timeout=60)

        assert outcome.get("done") is True, f"清空后重跑应正常完成：{outcome}"
        assert ctx.dimension_results, "重跑应重新产出维度结果"
