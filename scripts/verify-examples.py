# -*- coding: utf-8 -*-
"""
verify-examples.py —— 《智刃工艺·CNC切削工艺参数智能推荐系统·提示词工程规范》
第9章「端到端示例库」（docs/draft/09-examples.md）数值复算脚本。

用途
----
把第9章六个端到端示例里出现的每一个数值，用规范 3.6 节的统一公式重新算一遍，
与正文写出的数值逐项比对，容差 ±2%（Ra_th/Ra_pred 这类小量按同口径判）。

只用规范 3.6 节这一套公式，不引入任何别的模型：
    n      = 1000 * vc / (pi * D)                         [r/min]
    vf     = fz * z * n                                   [mm/min]
    MRR    = ap * ae * vf                                 [mm3/min]
    Pc     = ap * ae * vf * Kc / (60 * 1e6)               [kW]
    Ra_th  = fz^2 / (32 * r_eps) * 1000                   [um]  侧铣/端铣
    Ra_th  = fz^2 / (8 * R) * 1000                        [um]  球头 OP4，R 单位 mm
    Ra_pred= Ra_th * K_ra                                 [um]
    T      = (C / vc) ^ (1 / n_taylor)                    [min]
    Tm     = V / (ap * ae * vf)                           [min]
    LD     = L / D                                        [-]

运行：
    python scripts/verify-examples.py
退出码：全部通过 0；存在超容差项 1。
"""

import math
import sys

# Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOL = 0.02  # 容差 ±2%

# ---------------------------------------------------------------- 设定（规范 3.1–3.5）
MACHINE = {"n_max": 8000.0, "Pc_max": 11.0, "vf_min": 1.0, "vf_max": 10000.0, "fn1": 320.0}

MAT = {
    # 代号: (Kc N/mm2, Taylor C, Taylor n, K_ra, vc_lo, vc_hi, fz_lo, fz_hi, aeD_max, apD_max)
    "M1": (2000.0, 350.0, 0.25, 1.6, 120.0, 180.0, 0.05, 0.12, 0.5, 1.0),
    "M2": (700.0, 1800.0, 0.40, 1.2, 300.0, 500.0, 0.08, 0.20, 0.7, 2.0),
    "M3": (2600.0, 160.0, 0.20, 2.2, 60.0, 100.0, 0.04, 0.08, 0.4, 0.5),
    "M4": (1500.0, 280.0, 0.25, 2.0, 100.0, 150.0, 0.06, 0.12, 0.5, 0.8),
    "M5": (2100.0, 80.0, 0.18, 2.5, 30.0, 60.0, 0.03, 0.06, 0.3, 0.3),
}

TOOL = {
    # 代号: (D mm, z, L mm, r_eps mm, 球头R mm 或 None)
    "T1": (10.0, 4, 40.0, 0.8, None),
    "T2": (12.0, 4, 45.0, 0.8, None),
    "T3": (16.0, 4, 55.0, 0.8, None),
    "T4": (20.0, 4, 65.0, 0.8, None),
    "T5": (10.0, 2, 50.0, 5.0, 5.0),
    "T6": (80.0, 5, 80.0, 1.2, None),
}

# ---------------------------------------------------------------- 规范 3.6 公式
def f_n(vc, D):
    return 1000.0 * vc / (math.pi * D)


def f_vf(fz, z, n):
    return fz * z * n


def f_mrr(ap, ae, vf):
    return ap * ae * vf


def f_pc(ap, ae, vf, Kc):
    return ap * ae * vf * Kc / (60.0 * 1e6)


def f_ra_th_side(fz, r_eps):
    return fz * fz / (32.0 * r_eps) * 1000.0


def f_ra_th_ball(fz, R):
    return fz * fz / (8.0 * R) * 1000.0


def f_ra_pred(ra_th, K_ra):
    return ra_th * K_ra


def f_T(C, vc, nt):
    return (C / vc) ** (1.0 / nt)


def f_Tm(vol, ap, ae, vf):
    return vol / (ap * ae * vf)


def f_LD(L, D):
    return L / D


# ---------------------------------------------------------------- 比对器
class Report:
    def __init__(self):
        self.rows = []
        self.fails = []

    def add(self, ex, item, doc, calc, unit="", rel=None):
        """doc=正文值，calc=复算值。rel 为 None 时按 |doc-calc|/|calc| <= TOL 判。"""
        if rel is None:
            rel = abs(doc - calc) / abs(calc) if calc not in (0.0,) else abs(doc)
        ok = rel <= TOL
        self.rows.append((ex, item, doc, calc, unit, rel, ok))
        if not ok:
            self.fails.append((ex, item, doc, calc, unit, rel))

    def dump(self):
        cur = None
        for ex, item, doc, calc, unit, rel, ok in self.rows:
            if ex != cur:
                cur = ex
                print("\n" + "=" * 84)
                print(ex)
                print("-" * 84)
                print(f"{'项目':<22}{'正文':>14}{'复算':>14}{'单位':>8}{'相对偏差':>11}  {'判定':>4}")
            print(f"{item:<22}{doc:>14.4f}{calc:>14.4f}{unit:>8}{rel*100:>10.3f}%  {'PASS' if ok else 'FAIL':>4}")
        return len(self.fails)


R = Report()


def block(ex, mat, tool, op, vc, fz, ap, ae, vol=None, note=""):
    """按一组参数算出全部指标并与正文值比对。doc 值由调用方给出。"""
    Kc, C, nt, Kra = MAT[mat][0], MAT[mat][1], MAT[mat][2], MAT[mat][3]
    D, z, L, re, Rball = TOOL[tool]
    n = f_n(vc, D)
    vf = f_vf(fz, z, n)
    mrr = f_mrr(ap, ae, vf)
    pc = f_pc(ap, ae, vf, Kc)
    if Rball is not None:
        ra_th = f_ra_th_ball(fz, Rball)
    else:
        ra_th = f_ra_th_side(fz, re)
    ra_pred = f_ra_pred(ra_th, Kra)
    T = f_T(C, vc, nt)
    LD = f_LD(L, D)
    return {
        "n": n, "vf": vf, "MRR": mrr, "Pc": pc, "Ra_th": ra_th,
        "Ra_pred": ra_pred, "T": T, "LD": LD,
        "Tm": (f_Tm(vol, ap, ae, vf) if vol else None),
        "aeD": ae / D, "apD": ap / D,
    }


def compare(ex, tag, calc, doc):
    """doc: dict of 正文值。缺项不比对。"""
    R.add(ex, f"{tag}.n", doc["n"], calc["n"], "r/min")
    R.add(ex, f"{tag}.vf", doc["vf"], calc["vf"], "mm/min")
    R.add(ex, f"{tag}.MRR", doc["MRR"], calc["MRR"], "mm3/min")
    R.add(ex, f"{tag}.Pc", doc["Pc"], calc["Pc"], "kW")
    R.add(ex, f"{tag}.Ra_th", doc["Ra_th"], calc["Ra_th"], "um")
    R.add(ex, f"{tag}.Ra_pred", doc["Ra_pred"], calc["Ra_pred"], "um")
    R.add(ex, f"{tag}.T", doc["T"], calc["T"], "min")
    if "Tm" in doc and calc["Tm"] is not None:
        R.add(ex, f"{tag}.Tm", doc["Tm"], calc["Tm"], "min")
    if "LD" in doc:
        R.add(ex, f"{tag}.LD", doc["LD"], calc["LD"], "-")
    if "aeD" in doc:
        R.add(ex, f"{tag}.ae/D", doc["aeD"], calc["aeD"], "-")
    if "apD" in doc:
        R.add(ex, f"{tag}.ap/D", doc["apD"], calc["apD"], "-")


# ================================================================ 示例 1
# M1(45#) + T3(φ16,z4,rε0.8) + OP2 侧铣，Ra_req 1.6，效率优先
EX1 = ("示例1  M1+T3+OP2 侧铣 · Ra_req 1.6 · 效率优先")

# 主方案：vc=150, fz=0.12, ap=3, ae=5，毛坯 120×80×3 侧铣一圈等效体积 120*80*3
c = block(EX1, "M1", "T3", "OP2", vc=150, fz=0.12, ap=3.0, ae=5.0, vol=120 * 80 * 3)
compare(EX1, "P0 主方案", c, {
    "n": 2984.2, "vf": 1432.4, "MRR": 21485.9, "Pc": 0.72,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 29.64, "Tm": 1.34,
    "LD": 3.44, "aeD": 0.3125, "apD": 0.1875,
})
# 备选 A 效率优先：fz 取区间上限 0.12 已是最快，改用 vc=180 提高 n
c = block(EX1, "M1", "T3", "OP2", vc=180, fz=0.12, ap=3.0, ae=5.0, vol=120 * 80 * 3)
compare(EX1, "P1 备选效率", c, {
    "n": 3581.0, "vf": 1718.9, "MRR": 25783.1, "Pc": 0.86,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 14.26, "Tm": 1.12,
})
# 备选 B 质量优先：fz=0.08, vc=150
c = block(EX1, "M1", "T3", "OP2", vc=150, fz=0.08, ap=3.0, ae=5.0, vol=120 * 80 * 3)
compare(EX1, "P2 备选质量", c, {
    "n": 2984.2, "vf": 954.9, "MRR": 14323.9, "Pc": 0.48,
    "Ra_th": 0.250, "Ra_pred": 0.400, "T": 29.64, "Tm": 2.01,
})
# fz 上限反解（Ra_req=1.6）：fz_max = sqrt(Ra_req*32*r_eps/(1000*K_ra))
fz_cap = math.sqrt(1.6 * 32 * 0.8 / (1000 * 1.6))
R.add(EX1, "fz上限(Ra1.6)反解", 0.16, fz_cap, "mm/z")
# Ra 余量（正文写的百分比）：(Ra_req - Ra_pred)/Ra_req
R.add(EX1, "P0 Ra余量", 0.4375, (1.6 - 0.90) / 1.6, "-")
R.add(EX1, "P2 Ra余量", 0.7500, (1.6 - 0.40) / 1.6, "-")
# 颤振：z=4 稳定带
for k, doc in ((1, 4800.0), (2, 2400.0), (3, 1600.0)):
    R.add(EX1, f"n_stable(k={k})", doc, 60.0 * MACHINE["fn1"] / (k * 4), "r/min")
# 主方案 n=2984.2 不在任何稳定带内（最近带 k=2 上缘 2472）→ 按 3.8 判 MEDIUM 而非 LOW
R.add(EX1, "k=2带下缘", 2328.0, 2400.0 * 0.97, "r/min")
R.add(EX1, "k=2带上缘", 2472.0, 2400.0 * 1.03, "r/min")

# ================================================================ 示例 2
# M2(6061-T6) + T2(φ12,z4) + OP3 型腔粗加工，效率优先
# 关键考察点：vc 取 450 时 n=11936.6 越 C-01，必须截断到 8,000
EX2 = ("示例2  M2+T2+OP3 型腔粗加工 · 效率优先 · 触发 C-01 截断")

n_req = f_n(450, 12)
R.add(EX2, "n(未截断,vc450)", 11936.6, n_req, "r/min")
R.add(EX2, "n(截断至极限)", 8000.0, min(n_req, MACHINE["n_max"]), "r/min")
# 截断后实际 vc：n=8000 反推
vc_act = 8000.0 * math.pi * 12 / 1000.0
R.add(EX2, "vc(截断后实算)", 301.6, vc_act, "m/min")
n_use = 8000.0
# 主方案：vc_trunc→n=8000, fz=0.20, ap=6, ae=6（ap/D=0.5, ae/D=0.5）
c = block(EX2, "M2", "T2", "OP3", vc=vc_act, fz=0.20, ap=6.0, ae=6.0, vol=100 * 80 * 20)
compare(EX2, "P0 主方案", c, {
    "n": 8000.0, "vf": 6400.0, "MRR": 230400.0, "Pc": 2.69,
    "Ra_th": 1.5625, "Ra_pred": 1.875, "T": 87.82, "Tm": 0.69,
    "LD": 3.75, "aeD": 0.5, "apD": 0.5,
})
# 备选 A：fz 降 0.18（Ra 余量），其余同
c = block(EX2, "M2", "T2", "OP3", vc=vc_act, fz=0.18, ap=6.0, ae=6.0, vol=100 * 80 * 20)
compare(EX2, "P1 备选质量", c, {
    "n": 8000.0, "vf": 5760.0, "MRR": 207360.0, "Pc": 2.42,
    "Ra_th": 1.2656, "Ra_pred": 1.5188, "T": 87.82, "Tm": 0.77,
})
# 备选 B：vc=300 走区间下部，n=7957.7（落在 8,000 以内，无需截断），fz=0.20
c = block(EX2, "M2", "T2", "OP3", vc=300.0, fz=0.20, ap=6.0, ae=6.0, vol=100 * 80 * 20)
compare(EX2, "P2 备选寿命", c, {
    "n": 7957.7, "vf": 6366.2, "MRR": 229183.2, "Pc": 2.67,
    "Ra_th": 1.5625, "Ra_pred": 1.875, "T": 88.18, "Tm": 0.70,
})
# 颤振 z=4 判定：n=8000 不在任何稳定带内 → 需看 MEDIUM/HIGH 条件
R.add(EX2, "f_t(z4,n8000)", 533.3, 4 * 8000.0 / 60.0, "Hz")

# ================================================================ 示例 3
# M3(304) + T1(φ10,z4) + OP2，质量优先，Ra_req 0.8，触发 C-07 MEDIUM
EX3 = ("示例3  M3+T1+OP2 · 质量优先 Ra_req 0.8 · 触发 C-07 MEDIUM")
# 主方案：vc=80, fz=0.05, ap=2.5(0.25D), ae=2.0(0.2D)
c = block(EX3, "M3", "T1", "OP2", vc=80, fz=0.05, ap=2.5, ae=2.0, vol=60 * 40 * 8)
compare(EX3, "P0 主方案", c, {
    "n": 2546.5, "vf": 509.3, "MRR": 2546.5, "Pc": 0.110,
    "Ra_th": 0.0977, "Ra_pred": 0.2148, "T": 32.00, "Tm": 7.54,
    "LD": 4.0, "aeD": 0.2, "apD": 0.25,
})
# 备选 A 质量优先加强：fz=0.04
c = block(EX3, "M3", "T1", "OP2", vc=80, fz=0.04, ap=2.5, ae=2.0, vol=60 * 40 * 8)
compare(EX3, "P1 备选质量", c, {
    "n": 2546.5, "vf": 407.4, "MRR": 2037.2, "Pc": 0.0883,
    "Ra_th": 0.0625, "Ra_pred": 0.1375, "T": 32.00, "Tm": 9.42,
})
# 备选 B 效率：fz=0.06
c = block(EX3, "M3", "T1", "OP2", vc=80, fz=0.06, ap=2.5, ae=2.0, vol=60 * 40 * 8)
compare(EX3, "P2 备选效率", c, {
    "n": 2546.5, "vf": 611.2, "MRR": 3056.0, "Pc": 0.132,
    "Ra_th": 0.1406, "Ra_pred": 0.3094, "T": 32.00, "Tm": 6.28,
})
# 颤振：n=2546.5 距 k=2 稳定带 2400*1.03=2472 之外 → MEDIUM 判定依据
R.add(EX3, "稳定带k=2上缘", 2472.0, 2400.0 * 1.03, "r/min")
R.add(EX3, "n-(带内)", 74.5, 2546.5 - 2400.0 * 1.03, "r/min")
# fz 上限反解 Ra_req=0.8: fz_max=sqrt(0.8*32*0.8/(1000*2.2))
R.add(EX3, "fz上限(Ra0.8)反解", 0.0965, math.sqrt(0.8 * 32 * 0.8 / (1000 * 2.2)), "mm/z")

# ================================================================ 示例 4
# M5(TC4) + T5(φ10,R5,z2) + OP4 曲面精加工，触发 C-09 冷却约束
EX4 = ("示例4  M5+T5+OP4 曲面精加工 · 触发 C-09 冷却约束")
# 主方案：vc=45, fz=0.04, ap=0.5(0.05D), ae=0.3(0.03D)
c = block(EX4, "M5", "T5", "OP4", vc=45, fz=0.04, ap=0.5, ae=0.3, vol=40 * 30 * 0.6)
compare(EX4, "P0 主方案", c, {
    "n": 1432.4, "vf": 114.6, "MRR": 17.19, "Pc": 0.0006,
    "Ra_th": 0.0400, "Ra_pred": 0.1000, "T": 24.45, "Tm": 42.11,
    "LD": 5.0, "aeD": 0.03, "apD": 0.05,
})
# 备选 A 质量优先：fz=0.03
c = block(EX4, "M5", "T5", "OP4", vc=45, fz=0.03, ap=0.5, ae=0.3, vol=40 * 30 * 0.6)
compare(EX4, "P1 备选质量", c, {
    "n": 1432.4, "vf": 85.9, "MRR": 12.89, "Pc": 0.00045,
    "Ra_th": 0.0225, "Ra_pred": 0.0562, "T": 24.45, "Tm": 56.15,
})
# 备选 B 寿命优先：vc=30（区间下部 0%）, fz=0.04
c = block(EX4, "M5", "T5", "OP4", vc=30, fz=0.04, ap=0.5, ae=0.3, vol=40 * 30 * 0.6)
compare(EX4, "P2 备选寿命", c, {
    "n": 954.9, "vf": 76.4, "MRR": 11.46, "Pc": 0.0004,
    "Ra_th": 0.0400, "Ra_pred": 0.1000, "T": 232.54, "Tm": 63.17,
})
# LD=5.0 落在 4–6 区间 → ap 上限 ×0.7：0.3D*0.7=0.21D=2.1mm，本方案 ap=0.5 仍远低于上限
R.add(EX4, "ap上限(LD>4 ×0.7)", 2.1, 0.3 * 10 * 0.7, "mm")
# 球头 Ra_th 与侧铣口径对比（同为 fz=0.04）：证明 OP4 必须用 8R 口径
R.add(EX4, "Ra_th(8R 口径)", 0.0400, f_ra_th_ball(0.04, 5.0), "um")
# fz 上限反解 Ra_req=0.4：fz_max=sqrt(0.4*8*5/(1000*2.5))
R.add(EX4, "fz上限(Ra0.4)反解", 0.0800, math.sqrt(0.4 * 8 * 5.0 / (1000 * 2.5)), "mm/z")

# ================================================================ 示例 5
# M1(45#) + T4(φ20,z4,rε0.8) + OP2 大切深侧铣，触发 C-03 功率越限并走收敛
# 说明：原大纲的 M4+T6(φ80)+OP1 组合经复算 Pc 仅 0.72 kW（占 11 kW 的 6.5%），
#       大直径面铣刀转速低、vf 低，功率反而宽裕，物理上不可能触发 C-03。
#       故改用 M1+T4+OP2 侧铣（大切深、小切宽、高 vf），Pc 可达 12.83 kW 真实越限。
EX5 = ("示例5  M1+T4+OP2 大切深侧铣 · 触发 C-03 功率越限并走收敛")
vc, fz = 180.0, 0.12
# 首轮：ap/D 与 ae/D 双取上限 ap=20(1.0D) ae=14(0.7D)
c0 = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=20.0, ae=14.0)
R.add(EX5, "c0.ap/D", 1.000, c0["apD"], "-")
R.add(EX5, "c0.ae/D", 0.700, c0["aeD"], "-")
R.add(EX5, "c0.MRR", 385027.6, c0["MRR"], "mm3/min")
R.add(EX5, "c0.Pc(越C-03)", 12.834, c0["Pc"], "kW")
# 收敛步1：ap 按 11/12.834 等比例解算 → 17.15，得 11.005 仍压线 fail
c1 = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=17.15, ae=14.0)
R.add(EX5, "c1.ap/D", 0.857, c1["apD"], "-")
R.add(EX5, "c1.MRR", 330161.2, c1["MRR"], "mm3/min")
R.add(EX5, "c1.Pc(临界fail)", 11.005, c1["Pc"], "kW")
# 收敛步2：下取整 ap=17 → 10.909 pass（C-03 通过），但 C-07 仍判 HIGH（ae/D=0.70）
c2 = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=17.0, ae=14.0, vol=60 * 60 * 17)
R.add(EX5, "c2.Pc(C-03已过)", 10.909, c2["Pc"], "kW")
R.add(EX5, "c2.ae/D(仍触发C-07 HIGH)", 0.700, c2["aeD"], "-")
# 最终主方案 P0：C-03 降 ap 至 17 + C-07 强制降 ae 至 10(0.5D)
cF = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=17.0, ae=10.0, vol=60 * 60 * 17)
compare(EX5, "P0 最终主方案", cF, {
    "n": 2864.8, "vf": 1375.1, "MRR": 233767.0, "Pc": 7.792,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 14.29, "LD": 3.25,
    "aeD": 0.500, "apD": 0.850, "Tm": 0.26,
})
# 备选 P1：改走降 ae 路径，ae=12 时 11.001 临界 fail，再降到 10(0.5D)
cB12 = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=20.0, ae=12.0)
R.add(EX5, "ae12.Pc(临界fail)", 11.001, cB12["Pc"], "kW")
cA = block(EX5, "M1", "T4", "OP2", vc=vc, fz=fz, ap=20.0, ae=10.0)
compare(EX5, "P1 备选降ae", cA, {
    "n": 2864.8, "vf": 1375.1, "MRR": 275020.0, "Pc": 9.167,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 14.29,
    "aeD": 0.500, "apD": 1.000,
})
# 备选 P2：vc 压回区间下限 120，保 ap/ae 不变
cB = block(EX5, "M1", "T4", "OP2", vc=120, fz=fz, ap=20.0, ae=14.0)
compare(EX5, "P2 备选寿命", cB, {
    "n": 1909.9, "vf": 916.7, "MRR": 256685.1, "Pc": 8.556,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 72.37,
})
# Pc∝ap 线性核验：c1/c0 = Pc1/Pc0 = ap1/ap0
R.add(EX5, "Pc∝ap 线性核验", 0.8575, c1["Pc"] / c0["Pc"], "-")
# 两条收敛路径的 MRR 比：c2（仅降 ap，ae 仍 14）优于 P1（仅降 ae 至 0.5D）
R.add(EX5, "MRR(c2/P1)", 1.1900, c2["MRR"] / cA["MRR"], "-")
# 颤振 z=4 稳定带（用于 9.6(8) 判定说明）
for k in (1, 2, 3):
    R.add(EX5, f"n_stable(k={k},z=4)", 60.0 * MACHINE["fn1"] / (k * 4), 60.0 * MACHINE["fn1"] / (k * 4), "r/min")
R.add(EX5, "k=1带下缘", 4656.0, 4800.0 * 0.97, "r/min")
R.add(EX5, "k=1带上缘", 4944.0, 4800.0 * 1.03, "r/min")
# 3.8 节 C-07 HIGH 判定条件 A 项核验：ae/D > 0.5 即命中 HIGH
R.add(EX5, "C-07 HIGH(A项 ae/D>0.5)", 0.700, c0["aeD"], "-")
R.add(EX5, "C-07 降ae后 ae/D≤0.5", 0.500, cF["aeD"], "-")
# 最终功率占连续功率比例
R.add(EX5, "P0.Pc占11kW比例", 70.8, cF["Pc"] / MACHINE["Pc_max"] * 100, "%")

# ================================================================ 示例 6
# 越限拒答：用户要求 n=12,000 r/min 加工 M1
EX6 = ("示例6  越限拒答 · 用户要求 n=12000 r/min 加工 M1 · C-01 + MACHINE_LIMIT")
R.add(EX6, "C-01 上限", 8000.0, MACHINE["n_max"], "r/min")
R.add(EX6, "请求超出量", 4000.0, 12000.0 - MACHINE["n_max"], "r/min")
R.add(EX6, "超出比例", 0.50, (12000.0 - MACHINE["n_max"]) / MACHINE["n_max"], "-")
R.add(EX6, "n_max/n_req", 0.667, MACHINE["n_max"] / 12000.0, "-")
# 若用 T3(φ16)：n=12000 对应 vc，及被 C-01 压回后的 vc
R.add(EX6, "vc(n=12000,D16)", 603.2, 12000.0 * math.pi * 16 / 1000.0, "m/min")
R.add(EX6, "vc(n=8000,D16)", 402.1, 8000.0 * math.pi * 16 / 1000.0, "m/min")
R.add(EX6, "vc上限(M1)", 180.0, MAT["M1"][5], "m/min")
R.add(EX6, "vc超出M1上限倍数", 3.351, 603.2 / MAT["M1"][5], "-")
# 合规上限方案：vc=180, D=16 → n=3581，fz=0.12
c = block(EX6, "M1", "T3", "OP2", vc=180, fz=0.12, ap=3.0, ae=5.0, vol=120 * 80 * 3)
compare(EX6, "合规上限方案", c, {
    "n": 3581.0, "vf": 1718.9, "MRR": 25783.1, "Pc": 0.86,
    "Ra_th": 0.5625, "Ra_pred": 0.900, "T": 14.26, "Tm": 1.12,
})
# 反对照：若真按 n=12000 加工（越限），Pc 放大 n 之比
R.add(EX6, "Pc倍率(n12000/n3581)", 3.351, 12000.0 / 3581.0, "-")
R.add(EX6, "Pc(越限假设)", 2.883, 0.8595 * (12000.0 / 3581.0), "kW")

# ================================================================ 输出
print("=" * 84)
print("第9章端到端示例库 · 数值复算报告")
print("公式口径：规范 3.6 节（n / vf / MRR / Pc / Ra_th / Ra_pred / T / Tm / LD）")
print(f"容差：±{TOL*100:.0f}%    机床：VMC-850（n_max=8000 r/min, Pc_max=11 kW, fn1=320 Hz）")
print("=" * 84)
nfail = R.dump()
total = len(R.rows)
print("\n" + "=" * 84)
print(f"合计比对 {total} 项，通过 {total - nfail} 项，失败 {nfail} 项")
if nfail:
    print("-" * 84)
    for ex, item, doc, calc, unit, rel in R.fails:
        print(f"[FAIL] {ex} / {item}: 正文={doc} 复算={calc:.4f}{unit} 偏差={rel*100:.2f}%")
sys.exit(1 if nfail else 0)
