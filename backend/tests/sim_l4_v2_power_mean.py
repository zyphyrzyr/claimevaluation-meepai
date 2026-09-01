"""
L4 校准方案 v2：保留一票否决，同时让分数符合常人直觉
核心工具：幂平均（Power Mean / 广义平均）

    M_p(x1..xn) = ( 1/n · Σ xi^p )^(1/p)

  p = 1    算术平均（不惩罚短板）
  p = 0    几何平均（惩罚短板，保留一票否决）
  p = -1   调和平均（惩罚短板最狠）
  p -> -∞  取最小值（完全否决）

关键性质（这是本方案成立的原因）：
  1. 自洽性：所有维度取值相同时，M_p = 该值
     →「五个维度都打 80 分 = 最终 80 分」，符合常人直觉
  2. 一票否决：p ≤ 0 时，任一维度为 0 则 M_p = 0
     → 完全保留 v4 的短板否决语义
  3. 不均衡惩罚：维度差异越大，分数低于算术平均越多
     → 保留"短板拖垮整体"的产品叙事

运行：backend/ 目录 python tests/sim_l4_v2_power_mean.py
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import scoring
from core.config import SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH

EPS = 1e-9


# ---------------------------------------------------------- 幂平均

def power_mean(values, p: float) -> float:
    """幂平均；p=0 时退化为几何平均"""
    vs = [max(float(v), 0.0) for v in values]
    n = len(vs)
    if n == 0:
        return 0.0
    if abs(p) < EPS:                                  # 几何平均
        prod = 1.0
        for v in vs:
            prod *= v
            if prod == 0.0:
                return 0.0
        return prod ** (1.0 / n)
    if p < 0 and any(v <= EPS for v in vs):           # 一票否决
        return 0.0
    total = sum(v ** p for v in vs)
    if total <= EPS:
        return 0.0
    return (total / n) ** (1.0 / p)


# ---------------------------------------------------------- 候选方案

def current_model(r, i, p_, d, rec, c=1.0):
    """现状：全连乘（双重压缩）"""
    return scoring.calculate_overall_score(
        scoring.calculate_legal_feasibility(r, i, p_, c),
        scoring.calculate_business_expectation("要钱", damages_scale=d, recovery_ability=rec))


def scheme_a(r, i, p_, d, rec, c=1.0):
    """方案 A：轴内连乘 + 轴间几何平均（仅开一次方，压缩仍在）"""
    legal = scoring.calculate_legal_feasibility(r, i, p_, c)
    biz = scoring.calculate_business_expectation("要钱", damages_scale=d, recovery_ability=rec)
    return round(((legal / 100) * (biz / 100)) ** 0.5 * 100, 1)


def scheme_d(r, i, p_, d, rec, c=1.0, p=0.0):
    """
    方案 D（推荐）：分层幂平均
      法律轴 = M_p(权利, 侵权, 程序) × 修正系数
      业务轴 = M_p(判赔, 回款)
      最终   = M_p(法律轴, 业务轴)
    保留 v4 二维分层语义，四象限图可继续使用
    """
    legal = power_mean([r, i, p_], p) * c
    legal = max(0.0, min(legal, 100.0))
    biz = power_mean([d, rec], p)
    return round(power_mean([legal, biz], p), 1)


def scheme_flat(r, i, p_, d, rec, c=1.0, p=0.0):
    """方案 D-flat：不分层的五维幂平均（自洽性同样成立，但失去法律/业务二维语义）"""
    return round(power_mean([r, i, p_, d, rec], p) * c if c == 1.0
                 else min(power_mean([r, i, p_, d, rec], p) * c, 100.0), 1)


def scheme_b(r, i, p_, d, rec, c=1.0, w=0.6):
    """方案 B：加权加法（放弃一票否决）"""
    legal = (r + i + p_) / 3 * c
    biz = (d + rec) / 2
    return round(max(0.0, min(legal, 100)) * w + biz * (1 - w), 1)


SCHEMES = [
    ("现状（全连乘）", current_model, dict()),
    ("A 轴间几何平均", scheme_a, dict()),
    ("D 分层几何平均 p=0", scheme_d, dict(p=0.0)),
    ("D 分层幂平均 p=-0.5", scheme_d, dict(p=-0.5)),
    ("D 分层调和平均 p=-1", scheme_d, dict(p=-1.0)),
    ("D-flat 五维几何平均", scheme_flat, dict(p=0.0)),
    ("B 加权加法 0.6/0.4", scheme_b, dict(w=0.6)),
]


# ---------------------------------------------------------- 验证

def test_self_consistency():
    """自洽性：所有维度同分时，最终分应等于该分数"""
    print("\n" + "=" * 78)
    print("检验一 · 自洽性（所有维度打同样的分，最终分应该等于这个分）")
    print("=" * 78)
    header = f"{'方案':<24}" + "".join(f"{v:>9}" for v in (60, 70, 80, 90, 95))
    print(f"\n{header}")
    print("  " + "-" * 74)
    for name, fn, kw in SCHEMES:
        row = f"  {name:<24}"
        for v in (60, 70, 80, 90, 95):
            row += f"{fn(v, v, v, v, v, **kw):>9.1f}"
        print(row)
    print(f"\n  （理想：每个格子都等于表头的分数）")


def test_veto():
    """一票否决：任一维度归零，最终分必须为 0"""
    print("\n" + "=" * 78)
    print("检验二 · 一票否决（其他四维 90 分，某一维为 0）")
    print("=" * 78)
    print(f"\n  {'方案':<24}{'权利=0':>10}{'侵权=0':>10}{'程序=0':>10}{'判赔=0':>10}{'回款=0':>10}")
    print("  " + "-" * 74)
    for name, fn, kw in SCHEMES:
        row = f"  {name:<24}"
        for pos in range(5):
            args = [90, 90, 90, 90, 90]
            args[pos] = 0
            row += f"{fn(*args, **kw):>10.1f}"
        print(row)
    print(f"\n  （理想：全部为 0.0，表示一票否决成立）")


def test_short_board():
    """短板敏感度：四维 90、一维从 90 掉到 10，分数如何变化"""
    print("\n" + "=" * 78)
    print("检验三 · 短板敏感度（其他四维 90 分，回款能力从 90 递减）")
    print("=" * 78)
    levels = (90, 70, 50, 30, 10)
    print(f"\n  {'方案':<24}" + "".join(f"{v:>9}" for v in levels))
    print("  " + "-" * 74)
    for name, fn, kw in SCHEMES:
        row = f"  {name:<24}"
        for v in levels:
            row += f"{fn(90, 90, 90, 90, v, **kw):>9.1f}"
        print(row)
    print(f"\n  （回款能力崩塌时，分数应显著下滑但不至于立刻归零）")


def test_distribution():
    """全分布：各方案在 40-95 分全枚举下的档位占比"""
    print("\n" + "=" * 78)
    print("检验四 · 全分布（各维度 40-95 分，步长 5，全枚举）")
    print("=" * 78)

    vals = list(range(40, 96, 5))
    combos = []
    for r in vals:
        for i in vals:
            for p_ in vals:
                for d in vals:
                    for rec in vals:
                        combos.append((r, i, p_, d, rec))
    n = len(combos)

    def band(f):
        if f >= SCORE_THRESHOLD_GO:
            return "优先启动"
        if f >= SCORE_THRESHOLD_PATCH:
            return "补短板"
        return "暂缓"

    print(f"\n  样本 {n:,} 组（阈值仍为 {SCORE_THRESHOLD_GO}/{SCORE_THRESHOLD_PATCH}）\n")
    print(f"  {'方案':<24}{'中位分':>9}{'P90':>8}{'最高':>8}"
          f"{'优先启动':>11}{'补短板':>10}{'暂缓':>10}")
    print("  " + "-" * 84)
    for name, fn, kw in SCHEMES:
        finals = [fn(*c, **kw) for c in combos]
        s = sorted(finals)
        cnt = Counter(band(f) for f in finals)
        print(f"  {name:<24}{s[n//2]:>9.1f}{s[int(n*0.90)]:>8.1f}{s[-1]:>8.1f}"
              f"{cnt['优先启动']/n*100:>10.2f}%{cnt['补短板']/n*100:>9.2f}%"
              f"{cnt['暂缓']/n*100:>9.2f}%")

    # 推荐方案 + 阈值微调
    print(f"\n  推荐方案 D(几何) 配不同阈值的效果：")
    print(f"\n  {'阈值':<24}{'优先启动':>11}{'补短板':>10}{'暂缓':>10}")
    print("  " + "-" * 84)
    finals = [scheme_d(*c) for c in combos]
    for go, patch in ((75, 60), (70, 55), (65, 50), (60, 45)):
        cnt = Counter("优先启动" if f >= go else ("补短板" if f >= patch else "暂缓")
                      for f in finals)
        print(f"  {f'{go}/{patch}':<24}{cnt['优先启动']/n*100:>10.2f}%"
              f"{cnt['补短板']/n*100:>9.2f}%{cnt['暂缓']/n*100:>9.2f}%")


def test_realistic_cases():
    """几个直觉案例：看看分数是否符合法律人的判断"""
    print("\n" + "=" * 78)
    print("检验五 · 直觉案例（分数是否符合法律人的判断）")
    print("=" * 78)

    cases = [
        ("完美案件", (95, 95, 95, 95, 95),
         "权利稳固、侵权明确、赔偿充分、被告有钱 → 应优先启动"),
        ("优质案件", (88, 85, 90, 80, 85),
         "各维度良好 → 应优先启动"),
        ("证据有短板", (85, 60, 80, 75, 80),
         "侵权认定证据不足 → 应补短板后启动"),
        ("赢了拿不到钱", (90, 88, 90, 85, 15),
         "权利侵权都没问题，但被告是空壳公司 → 应暂缓"),
        ("权利基础有瑕疵", (35, 85, 85, 80, 85),
         "商标有撤三风险 → 应暂缓或补短板"),
        ("平庸案件", (65, 65, 65, 65, 65),
         "每样都一般 → 应补短板后启动"),
    ]

    for label, args, expect in cases:
        print(f"\n  【{label}】{args}  —  {expect}")
        for name, fn, kw in SCHEMES:
            f = fn(*args, **kw)
            b = ("优先启动" if f >= SCORE_THRESHOLD_GO
                 else "补短板" if f >= SCORE_THRESHOLD_PATCH else "暂缓")
            print(f"      {name:<24}{f:>7.1f}  {b}")


if __name__ == "__main__":
    print("=" * 78)
    print("L4 校准方案 v2：保留一票否决 + 分数符合常人直觉")
    print("=" * 78)
    test_self_consistency()
    test_veto()
    test_short_board()
    test_realistic_cases()
    test_distribution()

    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    print("""
  1. 幂平均 M_p 同时满足三个要求，这是纯连乘和加权加法都做不到的：
       · 自洽性 —— 所有维度打 v 分，最终就是 v 分（连乘做不到，80×5 维只剩 32.8）
       · 一票否决 —— p ≤ 0 时任一维度归零则总分为零（加权加法做不到）
       · 短板惩罚 —— 维度越不均衡，分数越低于算术平均（保留产品叙事）

  2. 推荐 p = 0（几何平均）：惩罚力度适中，数学性质最干净。
     若希望短板惩罚更狠（例如回款能力极差时要压得更低），可下调到 p = -0.5 或 -1。

  3. 保留 v4 的分层结构（法律轴 / 业务轴），四象限图和决策仪表盘无需改动，
     只需把两层的聚合函数从「连乘」换成「幂平均」。
""")
