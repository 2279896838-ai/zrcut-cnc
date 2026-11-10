"""十条硬约束校验器与越限收敛算法。

对应《提示词工程规范》3.7 节的 C-01 至 C-10、3.8 节的颤振判据，
以及 4.4 节的收敛优先级：先降 ap → 再降 ae → 再降 vc → 最后降 fz。

设计原则：本模块只做判定与收敛，不做参数推荐策略。判定结果必须给出
actual、limit、action 三元组，便于审计与前端展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from . import calc
from .domain import (
    CHATTER_BAND_TOLERANCE,
    CHATTER_LOBES,
    LD_SAFE_MAX,
    LD_WARN_MAX,
    MACHINE,
    THIN_WALL_MM,
    ChatterRisk,
    Material,
    Tool,
    chatter_stable_rpm,
)


class CheckId(str, Enum):
    """硬约束编号。"""

    C01 = "C-01"
    C02 = "C-02"
    C03 = "C-03"
    C04 = "C-04"
    C05 = "C-05"
    C06 = "C-06"
    C07 = "C-07"
    C08 = "C-08"
    C09 = "C-09"
    C10 = "C-10"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """单条约束的判定结果。

    Attributes:
        check_id: 约束编号。
        constraint: 约束描述。
        passed: 是否通过。
        actual: 实际值。
        limit: 限值。
        action: 越限时建议的收敛动作。
        detail: 判定细节。
        applicable: 是否适用；不适用时 passed 视为 True。
    """

    check_id: CheckId
    constraint: str
    passed: bool
    actual: Any
    limit: Any
    action: str = "none"
    detail: str = ""
    applicable: bool = True

    def to_dict(self) -> dict[str, Any]:
        """转字典，字段名采用 camelCase 以对齐对外 API 契约。"""
        return {
            "checkId": self.check_id.value,
            "constraint": self.constraint,
            "passed": self.passed,
            "actual": self.actual,
            "limit": self.limit,
            "action": self.action,
            "detail": self.detail,
            "applicable": self.applicable,
        }


@dataclass(slots=True)
class ValidationReport:
    """一次完整校验的报告。

    Attributes:
        results: 十条约束的判定结果。
        converged: 是否已收敛到全部通过。
        iterations: 收敛迭代次数。
        final_params: 收敛后的参数，未收敛时为最后一次尝试。
        history: 收敛过程记录。
    """

    results: list[CheckResult] = field(default_factory=list)
    converged: bool = False
    iterations: int = 0
    final_params: dict[str, float] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        """返回未通过且适用的约束。"""
        return [r for r in self.results if r.applicable and not r.passed]

    @property
    def passed_all(self) -> bool:
        """是否全部通过。"""
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "results": [r.to_dict() for r in self.results],
            "converged": self.converged,
            "iterations": self.iterations,
            "final_params": self.final_params,
            "history": self.history,
            "passedAll": self.passed_all,
            "failed": [r.check_id.value for r in self.failed],
        }


def classify_chatter(tool: Tool, rpm: float, ae: float, ap: float) -> tuple[ChatterRisk, str]:
    """按 3.8 节判定颤振风险等级。

    判据（简化单自由度叶瓣模型）：
        LOW:    转速落在任一稳定带内且 ae/D ≤ 0.5；
                或转速不在稳定带内但 ae/D ≤ 0.3 且 ap ≤ 0.5D（小切宽低风险）
        MEDIUM: 转速不在稳定带内，ae/D ≤ 0.5 但超出 LOW 的小切宽条件；
                或转速在稳定带内但 ae/D > 0.5
        HIGH:   转速不在稳定带内，且 (ae/D > 0.5 或 ap > 1.0D)

    Args:
        tool: 刀具对象。
        rpm: 主轴转速，r/min。
        ae: 径向切宽，mm。
        ap: 轴向切深，mm。

    Returns:
        (风险等级, 判定说明)。

    Note:
        该判据是单自由度简化叶瓣模型，只用于风险分级，不用于精确预测稳定域；
        真实稳定域须以切削试验或锤击模态测试标定。
    """
    bands: list[tuple[int, float, float, float]] = []
    in_band = False
    for lobe in CHATTER_LOBES:
        center = chatter_stable_rpm(tool.teeth, lobe)
        lo = center * (1 - CHATTER_BAND_TOLERANCE)
        hi = center * (1 + CHATTER_BAND_TOLERANCE)
        bands.append((lobe, center, lo, hi))
        if lo <= rpm <= hi:
            in_band = True

    ae_d = ae / tool.diameter
    ap_d = ap / tool.diameter

    if in_band:
        if ae_d <= 0.5:
            lobe_hit = next(b[0] for b in bands if b[2] <= rpm <= b[3])
            return ChatterRisk.LOW, (
                f"n={rpm:.1f} 落在 k={lobe_hit} 稳定带内，且 ae/D={ae_d:.2f}≤0.5"
            )
        return ChatterRisk.MEDIUM, f"n={rpm:.1f} 在稳定带内，但 ae/D={ae_d:.2f}>0.5"

    # 以下为转速不在稳定带内的情形
    if ae_d > 0.5 or ap_d > 1.0:
        reasons = []
        if ae_d > 0.5:
            reasons.append(f"ae/D={ae_d:.2f}>0.5")
        if ap_d > 1.0:
            reasons.append(f"ap/D={ap_d:.2f}>1.0")
        band_txt = "/".join(f"{b[1]:.0f}" for b in bands)
        return ChatterRisk.HIGH, (
            f"n={rpm:.1f} 不在 z={tool.teeth} 稳定带 {band_txt} 内，且 "
            + "、".join(reasons)
            + "；该判据为单自由度简化叶瓣模型，仅作风险分级"
        )

    if ae_d <= 0.3 and ap_d <= 0.5:
        return ChatterRisk.LOW, (
            f"n={rpm:.1f} 不在稳定带内，但 ae/D={ae_d:.2f}≤0.3 且 ap/D={ap_d:.2f}≤0.5，"
            "小切宽低速切削振动能量低"
        )

    return ChatterRisk.MEDIUM, (
        f"n={rpm:.1f} 不在稳定带内，ae/D={ae_d:.2f}、ap/D={ap_d:.2f} 未达 HIGH 判据"
    )


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """校验所需的上下文参数。

    Attributes:
        ra_req: 粗糙度要求，μm。
        wall_thickness: 工件壁厚，mm；None 表示非薄壁。
        coolant_available: 是否具备内冷或高压外冷。
        vc_override_band: 是否放宽材料 vc 区间；默认 False。
        vc_floor: 允许的 vc 下限；当主轴极限迫使 vc 低于材料区间下限时，
            由上层传入该值以解除 C-05 的「低于下限即失败」判定。
    """

    ra_req: float
    wall_thickness: float | None = None
    coolant_available: bool = True
    vc_override_band: bool = False
    vc_floor: float | None = None

    def effective_vc_min(self, material: Material) -> float:
        """返回考虑主轴极限后的有效 vc 下限。

        当材料推荐区间下限在该机床上会导致主轴超速时（例如铝合金 vc=300
        配 φ10 刀具需 n=9549 r/min，超过 8000 上限），主轴极限优先，
        有效下限降为由主轴上限反推的 vc 值。
        """
        floor = material.vc_min
        if self.vc_floor is not None:
            return min(floor, self.vc_floor)
        return floor

    def effective_vc_max(self, material: Material) -> float:
        """返回有效 vc 上限。"""
        return material.vc_max


def validate(
    material: Material,
    tool: Tool,
    *,
    vc: float,
    fz: float,
    ap: float,
    ae: float,
    ctx: ValidationContext,
) -> list[CheckResult]:
    """执行 C-01 至 C-10 全部校验。

    Args:
        material: 材料对象。
        tool: 刀具对象。
        vc: 切削速度，m/min。
        fz: 每齿进给，mm/z。
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        ctx: 校验上下文。

    Returns:
        十条约束的判定结果列表。
    """
    res = calc.compute_all(material, tool, vc=vc, fz=fz, ap=ap, ae=ae)
    results: list[CheckResult] = []

    # C-01 主轴转速上限
    c01_pass = res.n <= MACHINE.spindle_max_rpm
    results.append(
        CheckResult(
            CheckId.C01,
            f"n ≤ {MACHINE.spindle_max_rpm:.0f} r/min",
            c01_pass,
            round(res.n, 1),
            MACHINE.spindle_max_rpm,
            "降 vc" if not c01_pass else "none",
            f"为上限的 {res.n / MACHINE.spindle_max_rpm * 100:.1f}%",
        )
    )

    # C-02 进给范围
    c02_pass = MACHINE.feed_min_mmpm <= res.vf <= MACHINE.feed_max_mmpm
    results.append(
        CheckResult(
            CheckId.C02,
            f"{MACHINE.feed_min_mmpm:.0f} ≤ vf ≤ {MACHINE.feed_max_mmpm:.0f} mm/min",
            c02_pass,
            round(res.vf, 1),
            MACHINE.feed_max_mmpm,
            "调整 fz" if not c02_pass else "none",
            "合规" if c02_pass else "超出台架进给范围",
        )
    )

    # C-03 主轴功率
    c03_pass = res.pc <= MACHINE.power_continuous_kw
    results.append(
        CheckResult(
            CheckId.C03,
            f"Pc ≤ {MACHINE.power_continuous_kw:.0f} kW",
            c03_pass,
            round(res.pc, 3),
            MACHINE.power_continuous_kw,
            "降 ap" if not c03_pass else "none",
            (
                f"余量 {MACHINE.power_continuous_kw - res.pc:.3f} kW"
                if c03_pass
                else f"超出 {res.pc - MACHINE.power_continuous_kw:.3f} kW"
            ),
        )
    )

    # C-04 悬伸刚性
    ld = tool.overhang_ratio
    c04_applicable = True
    c04_pass = ld <= LD_WARN_MAX
    if ld <= LD_SAFE_MAX:
        c04_detail = f"L/D={ld:.2f}≤{LD_SAFE_MAX}，无需折减"
    elif ld <= LD_WARN_MAX:
        c04_detail = f"L/D={ld:.2f} 处于 {LD_SAFE_MAX}-{LD_WARN_MAX}，ap 上限折减 0.7"
    else:
        c04_detail = f"L/D={ld:.2f}>{LD_WARN_MAX}，刚性严重不足，转人工"
    results.append(
        CheckResult(
            CheckId.C04,
            f"L/D ≤ {LD_SAFE_MAX:.1f}",
            c04_pass,
            round(ld, 3),
            LD_SAFE_MAX,
            "none" if c04_pass else "换刀或缩短悬伸",
            c04_detail,
            c04_applicable,
        )
    )

    # C-05 材料参数区间
    vc_lo = ctx.effective_vc_min(material)
    vc_hi = ctx.effective_vc_max(material)
    vc_ok = vc_lo <= vc <= vc_hi
    fz_ok = material.fz_min <= fz <= material.fz_max
    if ctx.vc_override_band:
        vc_ok = True
    c05_pass = vc_ok and fz_ok
    parts = []
    if not vc_ok:
        parts.append(f"vc={vc:g} 超出 {vc_lo:g}-{vc_hi:g}")
    if not fz_ok:
        parts.append(f"fz={fz:g} 超出 {material.fz_min:g}-{material.fz_max:g}")
    note = ""
    if vc_lo < material.vc_min:
        note = f"（下标已按主轴上限放宽至 {vc_lo:.1f}）"
    results.append(
        CheckResult(
            CheckId.C05,
            f"vc {vc_lo:g}-{vc_hi:g} m/min, fz {material.fz_min:g}-{material.fz_max:g} mm/z",
            c05_pass,
            f"vc={vc:g},fz={fz:g}",
            f"vc {vc_lo:g}-{vc_hi:g},fz {material.fz_min:g}-{material.fz_max:g}",
            "收敛到边界" if not c05_pass else "none",
            ("合规" + note) if c05_pass else "；".join(parts),
        )
    )

    # C-06 切深切宽
    ap_d = ap / tool.diameter
    ae_d = ae / tool.diameter
    ap_lim = material.ap_d_max
    ae_lim = material.ae_d_max
    if LD_SAFE_MAX < ld <= LD_WARN_MAX:
        ap_lim_effective = ap_lim * 0.7
    else:
        ap_lim_effective = ap_lim
    if ctx.wall_thickness is not None and 0 < ctx.wall_thickness < THIN_WALL_MM:
        ap_lim_effective *= 0.5
    c06_pass = ap_d <= ap_lim_effective + 1e-9 and ae_d <= ae_lim + 1e-9
    results.append(
        CheckResult(
            CheckId.C06,
            f"ap/D≤{ap_lim_effective:g}, ae/D≤{ae_lim:g}",
            c06_pass,
            f"{ap_d:.3f}/{ae_d:.3f}",
            f"{ap_lim_effective:g}/{ae_lim:g}",
            "降 ap 或降 ae" if not c06_pass else "none",
            "合规" if c06_pass else f"ap/D={ap_d:.3f} 或 ae/D={ae_d:.3f} 超限",
        )
    )

    # C-07 颤振风险
    risk, why = classify_chatter(tool, res.n, ae, ap)
    c07_pass = risk is not ChatterRisk.HIGH
    results.append(
        CheckResult(
            CheckId.C07,
            "颤振风险不高于 MEDIUM",
            c07_pass,
            risk.value,
            "LOW/MEDIUM/HIGH",
            "收敛稳定带或降 ap" if not c07_pass else "none",
            why,
        )
    )

    # C-08 质量达成
    c08_pass = res.ra_pred <= ctx.ra_req + 1e-9
    results.append(
        CheckResult(
            CheckId.C08,
            f"Ra_pred ≤ {ctx.ra_req:g} μm",
            c08_pass,
            round(res.ra_pred, 3),
            ctx.ra_req,
            "降 fz 或换刀" if not c08_pass else "none",
            (
                f"余量 {ctx.ra_req - res.ra_pred:.3f} μm"
                if c08_pass
                else f"超出 {res.ra_pred - ctx.ra_req:.3f} μm"
            ),
        )
    )

    # C-09 冷却适配
    if material.requires_coolant:
        c09_applicable = True
        c09_pass = ctx.coolant_available
        c09_detail = (
            f"{material.name} 须内冷或高压外冷，当前{'已具备' if c09_pass else '不具备'}"
        )
    else:
        c09_applicable = False
        c09_pass = True
        c09_detail = f"{material.name} 不强制内冷，建议大流量冲刷"
    results.append(
        CheckResult(
            CheckId.C09,
            "M3/M5 须内冷或高压外冷",
            c09_pass,
            "具备" if ctx.coolant_available else "不具备",
            "具备",
            "none" if c09_pass else "停机并接入内冷",
            c09_detail,
            c09_applicable,
        )
    )

    # C-10 装夹刚性
    # C-10 装夹刚性（薄壁）
    # 与 C-06 共用 calc.ap_upper_bound，避免同一物理量出现两套折减口径。
    if ctx.wall_thickness is not None and 0 < ctx.wall_thickness < THIN_WALL_MM:
        c10_applicable = True
        ap_thin_limit = calc.ap_upper_bound(material, tool, ctx.wall_thickness)
        c10_pass = ap <= ap_thin_limit + 1e-9
        c10_detail = (
            f"薄壁 {ctx.wall_thickness:g}mm，ap 上限折减为 {ap_thin_limit:.2f}mm"
            f"（含悬伸折减后统一上限）"
        )
        c10_action = "none" if c10_pass else "降 ap 至折减上限"
        c10_actual = round(ap, 3)
        c10_limit = round(ap_thin_limit, 3)
    else:
        c10_applicable = False
        c10_pass = True
        c10_detail = "非薄壁件，不适用"
        c10_action = "none"
        c10_actual = round(ap, 3)
        c10_limit = "not_applicable"
    results.append(
        CheckResult(
            CheckId.C10,
            f"薄壁件（<{THIN_WALL_MM:g}mm）ap 折减 0.5",
            c10_pass,
            c10_actual,
            c10_limit,
            c10_action,
            c10_detail,
            c10_applicable,
        )
    )

    return results


def _diagnose(
    material: Material, tool: Tool, *, vc: float, fz: float, ap: float, ae: float, ctx: ValidationContext
) -> list[str]:
    """诊断当前参数下真正需要收敛的量。

    与简单看「哪条约束失败」不同，这里判断的是「哪个自变量超限」。
    例如 C-06 失败可能由 ap/D 或 ae/D 引起，二者的收敛动作完全不同。

    Returns:
        需要下降的变量名列表，取值属于 {"ap", "ae", "vc", "fz"}，按 4.4 节优先级排序。
    """
    res = calc.compute_all(material, tool, vc=vc, fz=fz, ap=ap, ae=ae)
    need: set[str] = set()

    ap_d = ap / tool.diameter
    ae_d = ae / tool.diameter
    ld = tool.overhang_ratio

    ap_lim = material.ap_d_max
    if LD_SAFE_MAX < ld <= LD_WARN_MAX:
        ap_lim *= 0.7
    if ctx.wall_thickness is not None and 0 < ctx.wall_thickness < THIN_WALL_MM:
        ap_lim *= 0.5
    ae_lim = material.ae_d_max

    # C-01 主轴转速 / C-05 vc 上下限
    # 主轴转速超限优先于材料区间：即使 vc 已低于材料下限，仍须继续降以保护主轴
    vc_lo = ctx.effective_vc_min(material)
    vc_hi = ctx.effective_vc_max(material)
    if res.n > MACHINE.spindle_max_rpm:
        need.add("vc")
    elif vc > vc_hi or vc < vc_lo:
        need.add("vc")
    # C-02 进给上限
    if res.vf > MACHINE.feed_max_mmpm or res.vf < MACHINE.feed_min_mmpm:
        need.add("fz")
    # C-08 质量
    if res.ra_pred > ctx.ra_req + 1e-9:
        need.add("fz")
    # C-05 fz 上限
    if fz > material.fz_max or fz < material.fz_min:
        need.add("fz")
    # C-03 功率 -> 只能靠降 ap 或 ae
    if res.pc > MACHINE.power_continuous_kw:
        need.add("ap")
    # C-06 分别判定
    if ap_d > ap_lim + 1e-9:
        need.add("ap")
    if ae_d > ae_lim + 1e-9:
        need.add("ae")
    # C-07 颤振：ae/D > 0.5 或 ap/D > 1.0 或转速不在稳定带
    risk, _ = classify_chatter(tool, res.n, ae, ap)
    if risk is ChatterRisk.HIGH:
        if ae_d > 0.5:
            need.add("ae")
        elif ap_d > 1.0:
            need.add("ap")
        else:
            need.add("vc")
    return [k for k in ("ap", "ae", "vc", "fz") if k in need]


def converge(
    material: Material,
    tool: Tool,
    *,
    vc: float,
    fz: float,
    ap: float,
    ae: float,
    ctx: ValidationContext,
    max_iterations: int = 8,
) -> ValidationReport:
    """按 4.4 节优先级迭代收敛到全部硬约束通过。

    收敛顺序：降 ap → 降 ae → 降 vc → 最后降 fz。
    每轮只施加一个动作，且只下降「真正超限的自变量」，最多 max_iterations 轮。

    理由：ap 对 Pc 是线性影响且不改变 Ra_th，因此优先降 ap 代价最小；
    fz 直接决定 Ra 且与颤振耦合，放在最后。

    Args:
        material: 材料对象。
        tool: 刀具对象。
        vc: 初始切削速度，m/min。
        fz: 初始每齿进给，mm/z。
        ap: 初始轴向切深，mm。
        ae: 初始径向切宽，mm。
        ctx: 校验上下文。
        max_iterations: 最大迭代轮数。

    Returns:
        ValidationReport，包含最终参数与收敛历史。
    """
    report = ValidationReport()
    cur_vc, cur_fz, cur_ap, cur_ae = float(vc), float(fz), float(ap), float(ae)

    # 若材料推荐区间下限在该机床上会导致主轴超速（如铝合金配小直径刀具），
    # 则以主轴上限反推的 vc 作为有效下限，主轴极限优先于推荐区间。
    vc_from_spindle = calc.cutting_speed_from_rpm(MACHINE.spindle_max_rpm * 0.98, tool.diameter)
    if ctx.vc_floor is None and material.vc_min > vc_from_spindle:
        ctx = ValidationContext(
            ra_req=ctx.ra_req,
            wall_thickness=ctx.wall_thickness,
            coolant_available=ctx.coolant_available,
            vc_override_band=ctx.vc_override_band,
            vc_floor=vc_from_spindle,
        )

    for it in range(max_iterations + 1):
        results = validate(
            material, tool, vc=cur_vc, fz=cur_fz, ap=cur_ap, ae=cur_ae, ctx=ctx
        )
        failed = [r for r in results if r.applicable and not r.passed]
        report.history.append(
            {
                "iteration": it,
                "vc": round(cur_vc, 4),
                "fz": round(cur_fz, 4),
                "ap": round(cur_ap, 4),
                "ae": round(cur_ae, 4),
                "failed": [r.check_id.value for r in failed],
            }
        )
        if not failed:
            report.results = results
            report.converged = True
            report.iterations = it
            report.final_params = {"vc": cur_vc, "fz": cur_fz, "ap": cur_ap, "ae": cur_ae}
            return report

        if it == max_iterations:
            break

        need = _diagnose(
            material, tool, vc=cur_vc, fz=cur_fz, ap=cur_ap, ae=cur_ae, ctx=ctx
        )
        if not need:
            # 约束失败但诊断不出可下降的量，无法收敛
            break

        target = need[0]
        changed = False

        if target == "ap":
            res = calc.compute_all(material, tool, vc=cur_vc, fz=cur_fz, ap=cur_ap, ae=cur_ae)
            if res.pc > MACHINE.power_continuous_kw:
                # 按功率比例反解，并向下取整到 0.5mm 步长
                ratio = MACHINE.power_continuous_kw * 0.98 / res.pc
                cand = _floor_to(cur_ap * ratio, 0.5)
            else:
                cand = cur_ap * 0.8
            cap = calc.ap_upper_bound(material, tool, ctx.wall_thickness)
            new_ap = min(cand, cap)
            if new_ap < cur_ap - 1e-9:
                cur_ap = max(new_ap, 0.1)
                changed = True

        elif target == "ae":
            cap = calc.ae_upper_bound(material, tool)
            new_ae = min(cur_ae, cap)
            if new_ae < cur_ae - 1e-9:
                cur_ae = max(new_ae, 0.1)
                changed = True
            else:
                # 已达材料上限仍 HIGH，缩 ae 30%
                new_ae = max(cur_ae * 0.7, 0.1)
                if new_ae < cur_ae - 1e-9:
                    cur_ae = new_ae
                    changed = True

        elif target == "vc":
            res = calc.compute_all(material, tool, vc=cur_vc, fz=cur_fz, ap=cur_ap, ae=cur_ae)
            vc_lo = ctx.effective_vc_min(material)
            cands = [material.vc_max]
            if res.n > MACHINE.spindle_max_rpm:
                cands.append(
                    calc.cutting_speed_from_rpm(MACHINE.spindle_max_rpm * 0.98, tool.diameter)
                )
            new_vc = min([cur_vc] + cands)
            if new_vc < cur_vc - 1e-9:
                cur_vc = max(new_vc, min(vc_lo, new_vc))
                changed = True
            elif res.pc > MACHINE.power_continuous_kw:
                new_vc = max(cur_vc * 0.85, vc_lo)
                if new_vc < cur_vc - 1e-9:
                    cur_vc = new_vc
                    changed = True
            elif res.n > MACHINE.spindle_max_rpm:
                new_vc = max(cur_vc * 0.9, vc_lo)
                if new_vc < cur_vc - 1e-9:
                    cur_vc = new_vc
                    changed = True

        elif target == "fz":
            fz_cap = calc.max_fz_for_roughness(
                ctx.ra_req, tool.corner_radius, material.k_ra, tool.is_ball
            )
            new_fz = min(cur_fz, fz_cap, material.fz_max)
            if new_fz < cur_fz - 1e-9:
                cur_fz = max(new_fz, 0.001)
                changed = True
            else:
                new_fz = max(cur_fz * 0.85, 0.001)
                if new_fz < cur_fz - 1e-9:
                    cur_fz = new_fz
                    changed = True

        if not changed:
            break

    report.results = validate(
        material, tool, vc=cur_vc, fz=cur_fz, ap=cur_ap, ae=cur_ae, ctx=ctx
    )
    report.converged = not [
        r for r in report.results if r.applicable and not r.passed
    ]
    report.iterations = len(report.history) - 1
    report.final_params = {"vc": cur_vc, "fz": cur_fz, "ap": cur_ap, "ae": cur_ae}
    return report


def _floor_to(value: float, step: float) -> float:
    """向下取整到指定步长。"""
    if step <= 0:
        return value
    return int(value / step) * step
