"""确定性切削参数计算引擎。

本模块是系统内唯一允许产生切削数值的地方。所有函数均为纯函数：
同样的输入必然得到同样的输出，不依赖时间、随机数、网络或全局可变状态。

公式口径严格遵循《提示词工程规范》3.6 节，不得在此之外另立公式。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

from .domain import MACHINE, Material, Tool


class CalculationError(ValueError):
    """计算输入不合法时抛出。"""


@dataclass(frozen=True, slots=True)
class CuttingResult:
    """一次完整计算的输出。

    Attributes:
        vc: 切削速度，m/min。
        n: 主轴转速，r/min。
        fz: 每齿进给，mm/z。
        vf: 进给速度，mm/min。
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        mrr: 材料去除率，mm³/min。
        pc: 切削功率，kW。
        ra_th: 理论表面粗糙度，μm。
        ra_pred: 预测表面粗糙度，μm。
        tool_life: 刀具寿命，min。
        overhang_ratio: 悬伸比 L/D。
        tooth_pass_hz: 齿通过频率，Hz。
    """

    vc: float
    n: float
    fz: float
    vf: float
    ap: float
    ae: float
    mrr: float
    pc: float
    ra_th: float
    ra_pred: float
    tool_life: float
    overhang_ratio: float
    tooth_pass_hz: float

    def to_dict(self) -> dict[str, Any]:
        """转字典，便于序列化。"""
        return asdict(self)


def spindle_speed(vc: float, diameter: float) -> float:
    """由切削速度反算主轴转速。

    n = 1000 × vc / (π × D)

    Args:
        vc: 切削速度，m/min。
        diameter: 刀具直径，mm。

    Returns:
        主轴转速，r/min。

    Raises:
        CalculationError: 输入非正。
    """
    if vc <= 0:
        raise CalculationError(f"切削速度必须为正，当前 {vc}")
    if diameter <= 0:
        raise CalculationError(f"刀具直径必须为正，当前 {diameter}")
    return 1000.0 * vc / (math.pi * diameter)


def cutting_speed_from_rpm(rpm: float, diameter: float) -> float:
    """由主轴转速反算切削速度。

    vc = π × D × n / 1000

    Args:
        rpm: 主轴转速，r/min。
        diameter: 刀具直径，mm。

    Returns:
        切削速度，m/min。

    Raises:
        CalculationError: 输入非正。
    """
    if rpm <= 0:
        raise CalculationError(f"主轴转速必须为正，当前 {rpm}")
    if diameter <= 0:
        raise CalculationError(f"刀具直径必须为正，当前 {diameter}")
    return math.pi * diameter * rpm / 1000.0


def feed_rate(fz: float, teeth: int, rpm: float) -> float:
    """计算进给速度。

    vf = fz × z × n

    Args:
        fz: 每齿进给，mm/z。
        teeth: 齿数。
        rpm: 主轴转速，r/min。

    Returns:
        进给速度，mm/min。

    Raises:
        CalculationError: 输入非正。
    """
    if fz <= 0:
        raise CalculationError(f"每齿进给必须为正，当前 {fz}")
    if teeth <= 0:
        raise CalculationError(f"齿数必须为正整数，当前 {teeth}")
    if rpm <= 0:
        raise CalculationError(f"主轴转速必须为正，当前 {rpm}")
    return fz * teeth * rpm


def material_removal_rate(ap: float, ae: float, vf: float) -> float:
    """计算材料去除率。

    MRR = ap × ae × vf

    Args:
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        vf: 进给速度，mm/min。

    Returns:
        材料去除率，mm³/min。

    Raises:
        CalculationError: 输入非正。
    """
    for name, value in (("轴向切深", ap), ("径向切宽", ae), ("进给速度", vf)):
        if value <= 0:
            raise CalculationError(f"{name}必须为正，当前 {value}")
    return ap * ae * vf


def cutting_power(mrr: float, kc: float) -> float:
    """计算切削功率。

    Pc = MRR × Kc / (60 × 10⁶)

    Args:
        mrr: 材料去除率，mm³/min。
        kc: 比切削力，N/mm²。

    Returns:
        切削功率，kW。

    Raises:
        CalculationError: 输入非正。
    """
    if mrr <= 0:
        raise CalculationError(f"材料去除率必须为正，当前 {mrr}")
    if kc <= 0:
        raise CalculationError(f"比切削力必须为正，当前 {kc}")
    return mrr * kc / (60.0 * 1_000_000.0)


def theoretical_roughness(fz: float, corner_radius: float, is_ball: bool = False) -> float:
    """计算理论表面粗糙度。

    侧铣与端铣: Ra_th = fz² / (32 × rε) × 1000
    球头刀 OP4: Ra_th = fz² / (8 × R) × 1000

    Args:
        fz: 每齿进给，mm/z。
        corner_radius: 刀尖圆弧半径 rε 或球半径 R，mm。
        is_ball: 是否为球头刀。

    Returns:
        理论表面粗糙度，μm。

    Raises:
        CalculationError: 输入非正。
    """
    if fz <= 0:
        raise CalculationError(f"每齿进给必须为正，当前 {fz}")
    if corner_radius <= 0:
        raise CalculationError(f"刀尖圆弧半径必须为正，当前 {corner_radius}")
    divisor = 8.0 if is_ball else 32.0
    return fz * fz / (divisor * corner_radius) * 1000.0


def predicted_roughness(ra_th: float, k_ra: float) -> float:
    """由理论粗糙度推算实测预测值。

    Ra_pred = Ra_th × K_ra

    Args:
        ra_th: 理论粗糙度，μm。
        k_ra: 材料修正系数。

    Returns:
        预测粗糙度，μm。

    Raises:
        CalculationError: 输入非正。
    """
    if ra_th <= 0:
        raise CalculationError(f"理论粗糙度必须为正，当前 {ra_th}")
    if k_ra <= 0:
        raise CalculationError(f"粗糙度修正系数必须为正，当前 {k_ra}")
    return ra_th * k_ra


def tool_life(vc: float, taylor_c: float, taylor_n: float) -> float:
    """按扩展 Taylor 公式估算刀具寿命。

    T = (C / vc)^(1/n)

    Args:
        vc: 切削速度，m/min。
        taylor_c: Taylor 常数 C。
        taylor_n: Taylor 指数 n。

    Returns:
        刀具寿命，min。

    Raises:
        CalculationError: 输入非正。
    """
    if vc <= 0:
        raise CalculationError(f"切削速度必须为正，当前 {vc}")
    if taylor_c <= 0:
        raise CalculationError(f"Taylor 常数 C 必须为正，当前 {taylor_c}")
    if taylor_n <= 0:
        raise CalculationError(f"Taylor 指数 n 必须为正，当前 {taylor_n}")
    return (taylor_c / vc) ** (1.0 / taylor_n)


def machining_time(volume: float, ap: float, ae: float, vf: float) -> float:
    """按体积去除法计算加工时间。

    Tm = V / (ap × ae × vf)

    Args:
        volume: 待去除体积，mm³。
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        vf: 进给速度，mm/min。

    Returns:
        加工时间，min。

    Raises:
        CalculationError: 输入非正。
    """
    if volume <= 0:
        raise CalculationError(f"待去除体积必须为正，当前 {volume}")
    mrr = material_removal_rate(ap, ae, vf)
    return volume / mrr


def max_fz_for_roughness(ra_req: float, corner_radius: float, k_ra: float, is_ball: bool = False) -> float:
    """由粗糙度要求反解每齿进给上限。

    反解 Ra_pred = fz² / (divisor × r) × 1000 × k_ra ≤ ra_req

    Args:
        ra_req: 粗糙度要求，μm。
        corner_radius: 刀尖圆弧半径或球半径，mm。
        k_ra: 材料修正系数。
        is_ball: 是否球头刀。

    Returns:
        每齿进给上限，mm/z。

    Raises:
        CalculationError: 输入非正。
    """
    if ra_req <= 0:
        raise CalculationError(f"粗糙度要求必须为正，当前 {ra_req}")
    if corner_radius <= 0:
        raise CalculationError(f"刀尖圆弧半径必须为正，当前 {corner_radius}")
    if k_ra <= 0:
        raise CalculationError(f"粗糙度修正系数必须为正，当前 {k_ra}")
    divisor = 8.0 if is_ball else 32.0
    return math.sqrt(ra_req * divisor * corner_radius / (1000.0 * k_ra))


def tooth_pass_frequency(teeth: int, rpm: float) -> float:
    """计算齿通过频率。

    f_t = z × n / 60

    Args:
        teeth: 齿数。
        rpm: 主轴转速，r/min。

    Returns:
        齿通过频率，Hz。
    """
    if teeth <= 0:
        raise CalculationError(f"齿数必须为正整数，当前 {teeth}")
    if rpm <= 0:
        raise CalculationError(f"主轴转速必须为正，当前 {rpm}")
    return teeth * rpm / 60.0


def compute_all(
    material: Material,
    tool: Tool,
    *,
    vc: float,
    fz: float,
    ap: float,
    ae: float,
    volume: float | None = None,
) -> CuttingResult:
    """一次性计算全部工艺指标。

    这是上层业务的统一入口，保证所有派生量口径一致。

    Args:
        material: 材料对象。
        tool: 刀具对象。
        vc: 切削速度，m/min。
        fz: 每齿进给，mm/z。
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        volume: 待去除体积，mm³；为空时不计算加工时间。

    Returns:
        CuttingResult 实例。

    Raises:
        CalculationError: 任一输入不合法。
    """
    n = spindle_speed(vc, tool.diameter)
    vf = feed_rate(fz, tool.teeth, n)
    mrr = material_removal_rate(ap, ae, vf)
    pc = cutting_power(mrr, material.kc)
    ra_th = theoretical_roughness(fz, tool.corner_radius, tool.is_ball)
    ra_pred = predicted_roughness(ra_th, material.k_ra)
    life = tool_life(vc, material.taylor_c, material.taylor_n)
    return CuttingResult(
        vc=vc,
        n=n,
        fz=fz,
        vf=vf,
        ap=ap,
        ae=ae,
        mrr=mrr,
        pc=pc,
        ra_th=ra_th,
        ra_pred=ra_pred,
        tool_life=life,
        overhang_ratio=tool.overhang_ratio,
        tooth_pass_hz=tooth_pass_frequency(tool.teeth, n),
    )


def machining_time_for(volume: float, result: CuttingResult) -> float:
    """基于已有计算结果的加工时间。

    Args:
        volume: 待去除体积，mm³。
        result: 已算出的切削结果。

    Returns:
        加工时间，min。

    Raises:
        CalculationError: 体积非正。
    """
    if volume <= 0:
        raise CalculationError(f"待去除体积必须为正，当前 {volume}")
    if result.mrr <= 0:
        raise CalculationError("材料去除率必须为正")
    return volume / result.mrr


def ap_upper_bound(material: Material, tool: Tool, wall_thickness: float | None = None) -> float:
    """计算轴向切深上限。

    依次应用：材料 ap/D 上限 → 悬伸比折减 → 薄壁折减。

    Args:
        material: 材料对象。
        tool: 刀具对象。
        wall_thickness: 工件壁厚，mm；为空表示非薄壁件。

    Returns:
        轴向切深上限，mm。
    """
    ap = material.ap_d_max * tool.diameter
    ld = tool.overhang_ratio
    from .domain import LD_SAFE_MAX, LD_WARN_MAX, LONG_OVERHANG_AP_FACTOR, THIN_WALL_AP_FACTOR, THIN_WALL_MM

    if LD_SAFE_MAX < ld <= LD_WARN_MAX:
        ap *= LONG_OVERHANG_AP_FACTOR
    if wall_thickness is not None and 0 < wall_thickness < THIN_WALL_MM:
        ap *= THIN_WALL_AP_FACTOR
    return ap


def ae_upper_bound(material: Material, tool: Tool) -> float:
    """计算径向切宽上限。

    Args:
        material: 材料对象。
        tool: 刀具对象。

    Returns:
        径向切宽上限，mm。
    """
    return material.ae_d_max * tool.diameter
