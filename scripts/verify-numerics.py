#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立数值验算脚本。

刻意不复用 zrcut 的计算函数，而是按《提示词工程规范》3.6 节的公式原文
用纯数学重算一遍，再与系统输出逐项比对。这是「数值可验算」的最终证据：
即使 zrcut.calc 写错了，本脚本也会发现。

用法：
    python scripts/verify-numerics.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zrcut.service import ZrCutService  # noqa: E402  仅用于取系统输出作对照

TOL = 0.02  # 相对容差 ±2%

# 规范 3.2 节的材料参数，独立硬编码，不 import zrcut.domain
KC = {"M1": 2000.0, "M2": 700.0, "M3": 2600.0, "M4": 1500.0, "M5": 2100.0}
C_T = {"M1": 350.0, "M2": 1800.0, "M3": 160.0, "M4": 280.0, "M5": 80.0}
N_T = {"M1": 0.25, "M2": 0.40, "M3": 0.20, "M4": 0.25, "M5": 0.18}
K_RA = {"M1": 1.6, "M2": 1.2, "M3": 2.2, "M4": 2.0, "M5": 2.5}

# 规范 3.3 节的刀具参数
D = {"T1": 10.0, "T2": 12.0, "T3": 16.0, "T4": 20.0, "T5": 10.0, "T6": 80.0}
Z = {"T1": 4, "T2": 4, "T3": 4, "T4": 4, "T5": 2, "T6": 5}
R_EPS = {"T1": 0.8, "T2": 0.8, "T3": 0.8, "T4": 0.8, "T5": 5.0, "T6": 1.2}
IS_BALL = {"T1": False, "T2": False, "T3": False, "T4": False, "T5": True, "T6": False}

FAILS: list[str] = []
CHECKS = 0


def check(label: str, got: float, want: float, tol: float = TOL) -> None:
    """比对系统输出与独立复算值。"""
    global CHECKS
    CHECKS += 1
    if want == 0:
        ok = abs(got) < 1e-9
        dev = 0.0
    else:
        dev = abs(got - want) / abs(want)
        ok = dev <= tol
    status = "PASS" if ok else "FAIL"
    print(f"  {label:<30}{got:>14.4f}{want:>14.4f}{dev * 100:>9.2f}%  {status}")
    if not ok:
        FAILS.append(f"{label}: got={got} want={want} dev={dev:.2%}")


def recompute(m: str, t: str, vc: float, fz: float, ap: float, ae: float, vol: float | None):
    """按公式原文独立复算全部指标。"""
    n = 1000.0 * vc / (math.pi * D[t])
    vf = fz * Z[t] * n
    mrr = ap * ae * vf
    pc = mrr * KC[m] / (60.0 * 1_000_000.0)
    divisor = 8.0 if IS_BALL[t] else 32.0
    ra_th = fz * fz / (divisor * R_EPS[t]) * 1000.0
    ra_pred = ra_th * K_RA[m]
    life = (C_T[m] / vc) ** (1.0 / N_T[m])
    tm = (vol / mrr) if vol else None
    return dict(n=n, vf=vf, mrr=mrr, pc=pc, ra_th=ra_th, ra_pred=ra_pred,
                life=life, tm=tm)


def main() -> int:
    """主入口。"""
    global CHECKS

    print("=" * 78)
    print("智刃工艺 · 独立数值验算")
    print("公式来源：《提示词工程规范》3.6 节，纯数学复算，不复用 zrcut.calc")
    print("=" * 78)

    svc = ZrCutService()
    cases = [
        ("M1", "T3", "OP2", 1.6, 288000.0, "标准侧铣"),
        ("M2", "T2", "OP3", 3.2, 100000.0, "铝合金型腔"),
        ("M3", "T1", "OP2", 0.8, 50000.0, "不锈钢精铣"),
        ("M5", "T5", "OP4", 0.4, 20000.0, "钛合金球头"),
        ("M4", "T6", "OP1", 3.2, 400000.0, "铸铁面铣"),
    ]

    for m, t, op, ra, vol, label in cases:
        print()
        print(f"[{m}+{t}+{op}] {label}  Ra_req={ra} μm  V={vol:,.0f} mm³")
        r = svc.recommend(material=m, tool=t, operation=op, ra_req=ra, volume=vol)
        p = r["primary"]
        ref = recompute(m, t, p["vc"], p["fz"], p["ap"], p["ae"], vol)
        check("主轴转速 n (r/min)", p["n"], ref["n"])
        check("进给速度 vf (mm/min)", p["vf"], ref["vf"])
        check("材料去除率 MRR", p["mrr"], ref["mrr"])
        check("切削功率 Pc (kW)", p["pc"], ref["pc"])
        check("理论粗糙度 Ra_th (μm)", p["raTh"], ref["ra_th"])
        check("预测粗糙度 Ra_pred (μm)", p["raPred"], ref["ra_pred"])
        check("刀具寿命 T (min)", p["toolLife"], ref["life"])
        if ref["tm"] is not None and p.get("machiningTime") is not None:
            check("加工时间 Tm (min)", p["machiningTime"], ref["tm"])

    # 越限收敛链验算：M1+T4 大切深侧铣
    print()
    print("[M1+T4+OP2] 功率越限收敛链验算")
    v = svc.validate_params(
        material="M1", tool="T4", vc=180, fz=0.12, ap=20, ae=14, ra_req=1.6
    )
    fp = v["final_params"]
    ref = recompute("M1", "T4", fp["vc"], fp["fz"], fp["ap"], fp["ae"], None)
    check("收敛后 Pc (kW)", v["finalMetrics"]["pc"], ref["pc"])
    check("收敛后 Ra_pred (μm)", v["finalMetrics"]["raPred"], ref["ra_pred"])
    # 原始参数应超功率
    raw_pc = 20.0 * 14.0 * 0.12 * 4 * (1000.0 * 180.0 / (math.pi * 20.0)) * 2000.0 / 60e6
    print(f"  {'原始方案 Pc (kW)':<30}{raw_pc:>14.4f}{'(>11 越限)' if raw_pc > 11 else '':>14}")
    CHECKS += 1
    if not raw_pc > 11.0:
        FAILS.append(f"原始方案应越限但 Pc={raw_pc}")

    # 主轴极限优先性验算：铝合金 φ10 刀具
    print()
    print("[M2+T1] 主轴极限优先于材料区间")
    vc_from_spindle = 8000.0 * 0.98 * math.pi * D["T1"] / 1000.0
    n_at_material_min = 1000.0 * 300.0 / (math.pi * D["T1"])
    print(f"  {'材料下限 vc=300 对应 n':<30}{n_at_material_min:>14.1f}{'':>14}  {'超限' if n_at_material_min > 8000 else ''}")
    print(f"  {'主轴反推 vc 上限':<30}{vc_from_spindle:>14.1f}{'':>14}  m/min")
    v2 = svc.validate_params(
        material="M2", tool="T1", vc=480, fz=0.19, ap=20.0, ae=7.0, ra_req=1.6
    )
    CHECKS += 1
    n_final = v2["finalMetrics"]["n"]
    ok = n_final <= 8000.0
    print(f"  {'收敛后 n':<30}{n_final:>14.1f}{'':>14}  {'PASS' if ok else 'FAIL'}")
    if not ok:
        FAILS.append(f"收敛后 n={n_final} 仍超主轴上限")

    print()
    print("=" * 78)
    print(f"合计比对 {CHECKS} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print("  FAIL " + f)
        return 1
    print("全部数值可由规范公式独立复算通过（容差 ±2%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
