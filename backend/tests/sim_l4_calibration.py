"""
L4 分数模型校准仿真（纯规则，零 LLM）
枚举各维度分数组合 → 统计 final 分布与档位占比 → 对比候选校准方案
运行：backend/ 目录 python tests/sim_l4_calibration.py
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import scoring
from core.config import SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH

# 模拟真实评估中 LLM 打分的合理区间（极少出现 0-30 或 95+）
REALISTIC = list(range(40, 96, 5))     # 40,45,...,95


def legal(r, i, p, c=1.0):
    return scoring.calculate_legal_feasibility(r, i, p, c)


def biz_money(d, rec):
    return scoring.calculate_business_expectation("要钱", damages_scale=d, recovery_ability=rec)


def biz_fame(pv):
    return scoring.calculate_business_expectation("要名", precedent_value=pv)


def band(final):
    if final >= SCORE_THRESHOLD_GO:
        return "建议优先启动"
    if final >= SCORE_THRESHOLD_PATCH:
        return "补充短板后启动"
    return "暂缓"


def simulate_money():
    rows = []
    for r in REALISTIC:
        for i in REALISTIC:
            for p in REALISTIC:
                lg = legal(r, i, p)
                for d in REALISTIC:
                    for rec in REALISTIC:
                        bz = biz_money(d, rec)
                        rows.append(scoring.calculate_overall_score(lg, bz))
    return rows


def simulate_fame():
    rows = []
    for r in REALISTIC:
        for i in REALISTIC:
            for p in REALISTIC:
                lg = legal(r, i, p)
                for pv in REALISTIC:
                    rows.append(scoring.calculate_overall_score(lg, biz_fame(pv)))
    return rows


def report(name, finals):
    n = len(finals)
    cnt = Counter(band(f) for f in finals)
    finals_sorted = sorted(finals)
    print(f"\n{'='*62}")
    print(f"{name}  样本 {n:,} 组")
    print(f"{'='*62}")
    print(f"  中位数 {finals_sorted[n//2]:5.1f} | 均值 {sum(finals)/n:5.1f} "
          f"| 最高 {max(finals):5.1f} | 最低 {min(finals):5.1f}")
    for b in ("建议优先启动", "补充短板后启动", "暂缓"):
        c = cnt.get(b, 0)
        print(f"  {b:<10} {c:>8,} 组  {c/n*100:5.2f}%  {'█'*int(c/n*60)}")


def quantile_stats(finals, label):
    n = len(finals)
    s = sorted(finals)
    print(f"\n  [{label}] 分位：P50={s[n//2]:.1f}  P75={s[int(n*0.75)]:.1f}  "
          f"P90={s[int(n*0.90)]:.1f}  P99={s[int(n*0.99)]:.1f}  最大值={s[-1]:.1f}")


def calib_geometric(legal_v, biz_v):
    """方案 A：改几何均值（开方）"""
    return round((legal_v / 100 * biz_v / 100) ** 0.5 * 100, 1)


def calib_weighted(legal_v, biz_v, w=0.6):
    """方案 B：加权加法"""
    return round(legal_v * w + biz_v * (1 - w), 1)


def calib_lowered_threshold(finals, go, patch):
    """方案 C：保留乘法，下调阈值"""
    return Counter("建议优先启动" if f >= go else
                   ("补充短板后启动" if f >= patch else "暂缓") for f in finals)


def main():
    print("二维乘法模型分数分布仿真")
    print("说明：模拟 LLM 在各维度打出 40-95 分（步长 5）的全部组合，"
          "反映真实评估中可能出现的分数范围。")

    money = simulate_money()
    fame = simulate_fame()
    report("要钱路径（法律 3 维 × 判赔 × 回款）", money)
    report("要名路径（法律 3 维 × 判例价值）", fame)

    quantile_stats(money, "要钱")
    quantile_stats(fame, "要名")

    # ---------------- 达到各档位所需条件 ----------------
    print(f"\n{'='*62}")
    print("达到'建议优先启动'（≥%.0f 分）所需的最低条件" % SCORE_THRESHOLD_GO)
    print(f"{'='*62}")
    print("\n  【要钱】法律三维同分 s，判赔=回款=t 时：")
    print(f"    {'法律三维':>8} {'判赔/回款':>10} {'法律可行性':>12} {'业务预期':>10} {'决策分':>8}")
    found = False
    for s in range(95, 60, -5):
        for t in range(95, 60, -5):
            lg, bz = legal(s, s, s), biz_money(t, t)
            f = scoring.calculate_overall_score(lg, bz)
            if f >= SCORE_THRESHOLD_GO:
                print(f"    {s:>8} {t:>10} {lg:>12.1f} {bz:>10.1f} {f:>8.1f}")
                found = True
                break
        if found:
            break

    print("\n  【要名】法律三维同分 s，判例价值=pv 时：")
    found = False
    for s in range(95, 60, -5):
        for pv in range(95, 60, -5):
            lg = legal(s, s, s)
            bz = biz_fame(pv)
            f = scoring.calculate_overall_score(lg, bz)
            if f >= SCORE_THRESHOLD_GO:
                print(f"    {s:>8} {pv:>10} {lg:>12.1f} {bz:>10.1f} {f:>8.1f}")
                found = True
                break
        if found:
            break

    # ---------------- 候选校准方案对比 ----------------
    print(f"\n{'='*62}")
    print("候选校准方案对比（同一批样本下的档位分布）")
    print(f"{'='*62}")

    # 取一个抽样集合用于方案对比（避免 11^5 全枚举）
    sample = []
    for r in range(50, 96, 10):
        for i in range(50, 96, 10):
            for p in range(50, 96, 10):
                lg = legal(r, i, p)
                for d in range(50, 96, 10):
                    for rec in range(50, 96, 10):
                        sample.append((lg, biz_money(d, rec)))
    n = len(sample)

    print(f"\n对比样本：法律三维 × 判赔 × 回款 各取 50-95（步长 10），共 {n:,} 组\n")
    print(f"  {'方案':<28} {'优先启动':>10} {'补短板':>10} {'暂缓':>10}  {'中位分':>8}")
    print(f"  {'-'*70}")

    # 现状
    cur = [scoring.calculate_overall_score(a, b) for a, b in sample]
    c = Counter(band(f) for f in cur)
    print(f"  {'现状：乘法 + 阈值 75/60':<28} {c['建议优先启动']/n*100:9.2f}% "
          f"{c['补充短板后启动']/n*100:9.2f}% {c['暂缓']/n*100:9.2f}%  {sorted(cur)[n//2]:8.1f}")

    # 方案 A：几何均值
    geo = [calib_geometric(a, b) for a, b in sample]
    cg = Counter(band(f) for f in geo)
    print(f"  {'A：几何均值 + 阈值 75/60':<28} {cg['建议优先启动']/n*100:9.2f}% "
          f"{cg['补充短板后启动']/n*100:9.2f}% {cg['暂缓']/n*100:9.2f}%  {sorted(geo)[n//2]:8.1f}")

    # 方案 B：加权加法
    for w in (0.6, 0.5):
        wsum = [calib_weighted(a, b, w) for a, b in sample]
        cw = Counter(band(f) for f in wsum)
        print(f"  {'B：加权加法(法律%.1f) 阈值不变' % w:<28} {cw['建议优先启动']/n*100:9.2f}% "
              f"{cw['补充短板后启动']/n*100:9.2f}% {cw['暂缓']/n*100:9.2f}%  {sorted(wsum)[n//2]:8.1f}")

    # 方案 C：保留乘法，下调阈值
    for go, patch in ((45, 35), (40, 30), (35, 25)):
        cc = calib_lowered_threshold(cur, go, patch)
        print(f"  {'C：乘法不变 + 阈值 %d/%d' % (go, patch):<28} "
              f"{cc['建议优先启动']/n*100:9.2f}% {cc['补充短板后启动']/n*100:9.2f}% "
              f"{cc['暂缓']/n*100:9.2f}%  {sorted(cur)[n//2]:8.1f}")

    print(f"\n{'='*62}")
    print("结论摘要")
    print(f"{'='*62}")
    cm = Counter(band(f) for f in money)
    print(f"  1. 要钱路径 {len(money):,} 组组合中，'建议优先启动'占 "
          f"{cm['建议优先启动']/len(money)*100:.3f}%，'暂缓'占 {cm['暂缓']/len(money)*100:.1f}%")
    print(f"  2. 最高分仅 {max(money):.1f}（阈值 {SCORE_THRESHOLD_GO}），"
          f"≥{SCORE_THRESHOLD_GO} 的组合需五维同时接近满分")
    print(f"  3. 要名路径因只有两层乘法，分布明显好于要钱路径（中位 "
          f"{sorted(fame)[len(fame)//2]:.1f} vs {sorted(money)[len(money)//2]:.1f}）——"
          f"两条路径不可直接横向比较")


if __name__ == "__main__":
    main()
