"""参数推荐引擎。

实现《提示词工程规范》4.2 节的八步固定生成顺序与三种策略模式。
本模块只做「选择与串联」，所有数值由 calc 计算、由 constraints 校验，
符合规范中「模型不得自行计算」的数值纪律。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from . import calc
from .constraints import ValidationContext, ValidationReport, converge, validate
from .domain import (
    MACHINE,
    Confidence,
    Material,
    MaterialId,
    OperationId,
    Strategy,
    Tool,
    ToolId,
    get_material,
    get_operation,
    get_tool,
)


class RecommendationError(ValueError):
    """推荐输入不合法或无法给出可行方案时抛出。

    本类是 ValueError 的子类，便于调用方在不依赖业务错误码时也能捕获。
    服务层负责把它转换为带稳定错误码的 ZrCutError。
    """


class ToolOperationMismatchError(RecommendationError):
    """刀具与工序不匹配。"""


class RoughnessUnachievableError(RecommendationError):
    """给定的粗糙度要求无法用当前刀具达到。"""


class CoolantRequiredError(RecommendationError):
    """材料必须冷却但当前不具备冷却条件（C-09）。"""


@dataclass(frozen=True, slots=True)
class Plan:
    """一套完整方案。

    Attributes:
        label: 方案标签，如 P0 / P1 / P2。
        strategy: 采用的策略。
        vc: 切削速度，m/min。
        fz: 每齿进给，mm/z。
        ap: 轴向切深，mm。
        ae: 径向切宽，mm。
        n: 主轴转速，r/min。
        vf: 进给速度，mm/min。
        mrr: 材料去除率，mm³/min。
        pc: 切削功率，kW。
        ra_th: 理论粗糙度，μm。
        ra_pred: 预测粗糙度，μm。
        tool_life: 刀具寿命，min。
        machining_time: 加工时间，min；未给体积时为 None。
        chatter_risk: 颤振等级。
        confidence: 置信度。
        note: 方案说明。
    """

    label: str
    strategy: Strategy
    vc: float
    fz: float
    ap: float
    ae: float
    n: float
    vf: float
    mrr: float
    pc: float
    ra_th: float
    ra_pred: float
    tool_life: float
    machining_time: float | None
    chatter_risk: str
    confidence: Confidence
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转字典，字段名采用 camelCase 以对齐对外 API 契约。"""
        return {
            "label": self.label,
            "strategy": self.strategy.value,
            "vc": round(self.vc, 2),
            "fz": round(self.fz, 4),
            "ap": round(self.ap, 2),
            "ae": round(self.ae, 2),
            "n": round(self.n, 1),
            "vf": round(self.vf, 1),
            "mrr": round(self.mrr, 0),
            "pc": round(self.pc, 3),
            "raTh": round(self.ra_th, 4),
            "raPred": round(self.ra_pred, 3),
            "toolLife": round(self.tool_life, 2),
            "machiningTime": (
                round(self.machining_time, 3) if self.machining_time is not None else None
            ),
            "chatterRisk": self.chatter_risk,
            "confidence": self.confidence.value,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RecommendationRequest:
    """推荐请求。

    Attributes:
        material: 材料代号。
        tool: 刀具代号。
        operation: 工序代号。
        ra_req: 粗糙度要求，μm。
        strategy: 策略；None 表示按质量要求自动选。
        volume: 待去除体积，mm³；None 则不计算工时。
        wall_thickness: 工件壁厚，mm；None 表示非薄壁。
        coolant_available: 是否具备内冷或高压外冷。
    """

    material: MaterialId
    tool: ToolId
    operation: OperationId
    ra_req: float = 3.2
    strategy: Strategy | None = None
    volume: float | None = None
    wall_thickness: float | None = None
    coolant_available: bool = True

    def __post_init__(self) -> None:
        if self.ra_req <= 0:
            raise RecommendationError(f"粗糙度要求必须为正，当前 {self.ra_req}")
        if self.volume is not None and self.volume <= 0:
            raise RecommendationError(f"去除体积必须为正，当前 {self.volume}")
        if self.wall_thickness is not None and self.wall_thickness <= 0:
            raise RecommendationError(f"壁厚必须为正，当前 {self.wall_thickness}")


@dataclass(slots=True)
class Recommendation:
    """推荐结果。

    Attributes:
        request: 原始请求。
        primary: 主方案。
        alternatives: 两套备选方案。
        validation: 主方案的校验报告。
        warnings: 风险提示列表。
        material: 材料对象。
        tool: 刀具对象。
        rationality: 参数合理性说明。
    """

    request: RecommendationRequest
    primary: Plan
    alternatives: list[Plan] = field(default_factory=list)
    validation: ValidationReport | None = None
    warnings: list[str] = field(default_factory=list)
    material: Material | None = None
    tool: Tool | None = None
    rationality: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "request": {
                "material": self.request.material.value,
                "tool": self.request.tool.value,
                "operation": self.request.operation.value,
                "raReq": self.request.ra_req,
                "strategy": (
                    self.request.strategy.value if self.request.strategy else None
                ),
                "volume": self.request.volume,
            },
            "primary": self.primary.to_dict(),
            "alternatives": [p.to_dict() for p in self.alternatives],
            "validation": self.validation.to_dict() if self.validation else None,
            "warnings": self.warnings,
            "rationality": self.rationality,
        }


# 各策略下的 vc 区间取值位置（0=下限, 1=上限）
# 刻意不取 0/1 端点：参数落在区间端点意味着材料手册边界值，属于需要 MEDIUM
# 置信度的情形；取 0.9/0.1 可在保留策略意图的同时留出安全余量。
_VC_POSITION: dict[Strategy, float] = {
    Strategy.EFFICIENCY: 0.90,
    Strategy.QUALITY: 0.50,
    Strategy.LIFE: 0.10,
    Strategy.BALANCED: 0.70,
}

# 各策略下的 fz 区间取值位置
_FZ_POSITION: dict[Strategy, float] = {
    Strategy.EFFICIENCY: 0.90,
    Strategy.QUALITY: 0.30,
    Strategy.LIFE: 0.60,
    Strategy.BALANCED: 0.70,
}


def _pick_in_band(lo: float, hi: float, pos: float) -> float:
    """在闭区间内按位置比例取值。"""
    pos = max(0.0, min(1.0, pos))
    return lo + (hi - lo) * pos


def _auto_strategy(ra_req: float, material: Material, tool: Tool) -> Strategy:
    """按质量要求与刀具类型自动选择策略。

    规则：Ra 要求显著严于该材料典型可达值（<理论粗糙度 1.5 倍）时走质量优先；
    钛合金与不锈钢因刀具成本高走寿命优先；其余走效率优先。
    """
    typical = calc.theoretical_roughness(0.08, tool.corner_radius, tool.is_ball) * material.k_ra
    if ra_req < typical * 1.5:
        return Strategy.QUALITY
    if material.code in (MaterialId.M5, MaterialId.M3):
        return Strategy.LIFE
    return Strategy.EFFICIENCY


def _make_plan(
    material: Material,
    tool: Tool,
    *,
    label: str,
    strategy: Strategy,
    vc: float,
    fz: float,
    ap: float,
    ae: float,
    volume: float | None,
    note: str,
) -> Plan:
    """由四个自变量构造方案，派生量全部走 calc。"""
    res = calc.compute_all(material, tool, vc=vc, fz=fz, ap=ap, ae=ae)
    tm = calc.machining_time_for(volume, res) if volume is not None else None
    from .constraints import classify_chatter

    risk, _ = classify_chatter(tool, res.n, ae, ap)
    return Plan(
        label=label,
        strategy=strategy,
        vc=vc,
        fz=fz,
        ap=ap,
        ae=ae,
        n=res.n,
        vf=res.vf,
        mrr=res.mrr,
        pc=res.pc,
        ra_th=res.ra_th,
        ra_pred=res.ra_pred,
        tool_life=res.tool_life,
        machining_time=tm,
        chatter_risk=risk.value,
        confidence=Confidence.MEDIUM,
        note=note,
    )


def _confidence_for(
    plan: Plan, validation: ValidationReport, ra_req: float, material: Material
) -> Confidence:
    """按 4.2 节口径判定置信度。

    HIGH:  全部硬约束通过、颤振 LOW、参数不在区间端点、Ra 余量 ≥20%
    MEDIUM: 全部硬约束通过，但命中任一边界或软告警条件
    LOW:   存在未通过项，或颤振 HIGH
    """
    if not validation.passed_all:
        return Confidence.LOW
    if plan.chatter_risk == "HIGH":
        return Confidence.LOW

    tol = 1e-6
    at_boundary = (
        abs(plan.vc - material.vc_max) < tol
        or abs(plan.vc - material.vc_min) < tol
        or abs(plan.fz - material.fz_max) < tol
        or abs(plan.fz - material.fz_min) < tol
    )
    margin = (ra_req - plan.ra_pred) / ra_req if ra_req > 0 else 0.0

    if at_boundary or margin < 0.2 or plan.chatter_risk == "MEDIUM":
        return Confidence.MEDIUM
    return Confidence.HIGH


def recommend(request: RecommendationRequest) -> Recommendation:
    """按八步固定顺序生成主方案与两套备选。

    步骤：
        ① 由材料+刀具查 vc 区间
        ② 算 n 并受 C-01 截断
        ③ 由 Ra_req 反解 fz 上限
        ④ 由 ap/D、ae/D 上限定切深切宽
        ⑤ 算 vf
        ⑥ 算 Pc 并受 C-03 校验
        ⑦ 算颤振等级
        ⑧ 输出主方案 + 两套备选

    Args:
        request: 推荐请求。

    Returns:
        Recommendation 实例。

    Raises:
        RecommendationError: 材料刀具工序组合不合理（如球头刀配侧铣）。
    """
    material = get_material(request.material)
    tool = get_tool(request.tool)
    operation = get_operation(request.operation)

    if request.operation not in (OperationId.OP2, OperationId.OP3) and tool.code not in operation.typical_tools:
        raise ToolOperationMismatchError(
            f"刀具 {tool.code.value}（{tool.kind}）不适用于工序 {operation.code.value}（{operation.name}）；"
            f"该工序典型刀具为 {', '.join(t.value for t in operation.typical_tools)}"
        )

    strategy = request.strategy or _auto_strategy(request.ra_req, material, tool)

    # 前置硬拦截：C-09 冷却适配。
    # 规范要求 M3/M5 必须内冷或高压外冷，不具备时不得给出参数建议，
    # 必须明确拒答并说明原因，而不是笼统地报「无可行方案」。
    if material.requires_coolant and not request.coolant_available:
        raise CoolantRequiredError(
            f"{material.code.value}（{material.name}）必须使用内冷或高压外冷，"
            f"当前冷却条件不具备，不得开切；"
            f"请先接入内冷装置或将冷却压力提升至 0.3-0.5 MPa 以上后重新推荐"
        )

    # ① vc 区间
    vc_lo, vc_hi = material.vc_min, material.vc_max

    # ② 算 n 并受 C-01 截断
    vc_target = _pick_in_band(vc_lo, vc_hi, _VC_POSITION[strategy])
    vc_cap = calc.cutting_speed_from_rpm(MACHINE.spindle_max_rpm * 0.98, tool.diameter)
    if vc_target > vc_cap:
        vc_target = max(min(vc_cap, vc_hi), vc_lo)

    # ③ 由 Ra_req 反解 fz 上限
    fz_cap_ra = calc.max_fz_for_roughness(
        request.ra_req, tool.corner_radius, material.k_ra, tool.is_ball
    )
    fz_hi = min(material.fz_max, fz_cap_ra)
    if fz_hi < material.fz_min:
        raise RoughnessUnachievableError(
            f"Ra {request.ra_req:g} μm 用 {tool.code.value} 无法达到："
            f"fz 上限反解为 {fz_cap_ra:.4f} mm/z，低于材料下限 {material.fz_min:g}；"
            f"建议改用更小 rε 的刀具或放宽 Ra 要求"
        )
    fz_target = _pick_in_band(material.fz_min, fz_hi, _FZ_POSITION[strategy])

    # ④ ap / ae 上限
    ap_cap = calc.ap_upper_bound(material, tool, request.wall_thickness)
    ae_cap = calc.ae_upper_bound(material, tool)

    ctx = ValidationContext(
        ra_req=request.ra_req,
        wall_thickness=request.wall_thickness,
        coolant_available=request.coolant_available,
    )

    def build(
        label: str,
        vc: float,
        fz: float,
        ap_frac: float,
        ae_frac: float,
        strat: Strategy,
        note: str,
    ) -> Plan:
        return _make_plan(
            material,
            tool,
            label=label,
            strategy=strat,
            vc=vc,
            fz=fz,
            ap=ap_cap * ap_frac,
            ae=ae_cap * ae_frac,
            volume=request.volume,
            note=note,
        )

    # ⑧ 主方案 + 两套备选（先构造，再逐套收敛校验）
    raw_plans: list[tuple[Plan, Strategy, str]] = []

    raw_plans.append(
        (build("P0", vc_target, fz_target, 1.0, 1.0, strategy, "按所选策略生成的主方案"), strategy, "主方案")
    )

    # 备选一：效率优先（若主方案已是效率优先，则给质量优先）
    if strategy is Strategy.EFFICIENCY:
        alt1_strategy = Strategy.QUALITY
    else:
        alt1_strategy = Strategy.EFFICIENCY
    alt1_vc = _pick_in_band(vc_lo, vc_hi, _VC_POSITION[alt1_strategy])
    alt1_vc = min(alt1_vc, vc_cap)
    alt1_fz = _pick_in_band(material.fz_min, fz_hi, _FZ_POSITION[alt1_strategy])
    raw_plans.append(
        (
            build(
                "P1",
                alt1_vc,
                alt1_fz,
                0.85 if alt1_strategy is Strategy.QUALITY else 1.0,
                0.85 if alt1_strategy is Strategy.QUALITY else 1.0,
                alt1_strategy,
                f"备选一：{_strategy_label(alt1_strategy)}",
            ),
            alt1_strategy,
            "备选一",
        )
    )

    # 备选二：寿命优先
    alt2_vc = _pick_in_band(vc_lo, vc_hi, _VC_POSITION[Strategy.LIFE])
    alt2_fz = _pick_in_band(material.fz_min, fz_hi, _FZ_POSITION[Strategy.LIFE])
    raw_plans.append(
        (
            build(
                "P2",
                alt2_vc,
                alt2_fz,
                0.9,
                0.9,
                Strategy.LIFE,
                "备选二：寿命优先，vc 压低换刀具寿命",
            ),
            Strategy.LIFE,
            "备选二",
        )
    )

    # ⑤⑥⑦ 逐方案收敛校验
    final_plans: list[Plan] = []
    primary_report: ValidationReport | None = None

    for raw, strat, role in raw_plans:
        report = converge(
            material,
            tool,
            vc=raw.vc,
            fz=raw.fz,
            ap=raw.ap,
            ae=raw.ae,
            ctx=ctx,
        )
        if not report.converged:
            continue
        p = report.final_params
        plan = _make_plan(
            material,
            tool,
            label=raw.label,
            strategy=strat,
            vc=p["vc"],
            fz=p["fz"],
            ap=p["ap"],
            ae=p["ae"],
            volume=request.volume,
            note=raw.note,
        )
        plan = Plan(
            **{
                **{
                    f: getattr(plan, f)
                    for f in (
                        "label",
                        "strategy",
                        "vc",
                        "fz",
                        "ap",
                        "ae",
                        "n",
                        "vf",
                        "mrr",
                        "pc",
                        "ra_th",
                        "ra_pred",
                        "tool_life",
                        "machining_time",
                        "chatter_risk",
                        "note",
                    )
                },
                "confidence": _confidence_for(plan, report, request.ra_req, material),
            }
        )
        final_plans.append(plan)
        if role == "主方案":
            primary_report = report

    if not final_plans:
        raise RecommendationError(
            "在给定约束下无法找到可行参数组合，请放宽 Ra 要求或更换刀具"
        )

    primary = final_plans[0]
    alternatives = final_plans[1:]

    warnings = _build_warnings(primary, primary_report, request, material, tool)
    rationality = _build_rationality(primary, request, material, tool)

    return Recommendation(
        request=request,
        primary=primary,
        alternatives=alternatives,
        validation=primary_report,
        warnings=warnings,
        material=material,
        tool=tool,
        rationality=rationality,
    )


def _strategy_label(strategy: Strategy) -> str:
    """策略中文标签。"""
    return {
        Strategy.EFFICIENCY: "效率优先",
        Strategy.QUALITY: "质量优先",
        Strategy.LIFE: "寿命优先",
        Strategy.BALANCED: "均衡",
    }[strategy]


def _build_warnings(
    plan: Plan,
    report: ValidationReport | None,
    request: RecommendationRequest,
    material: Material,
    tool: Tool,
) -> list[str]:
    """构造风险提示列表。"""
    out: list[str] = []
    if plan.chatter_risk == "HIGH":
        out.append(
            f"颤振风险 HIGH：n={plan.n:.0f} r/min 不在稳定带内且切宽比过大，必须试切首件并监听异响"
        )
    elif plan.chatter_risk == "MEDIUM":
        out.append(f"颤振风险 MEDIUM：n={plan.n:.0f} r/min 不在稳定带中心，建议首件试切监听异响")
    if plan.pc > MACHINE.power_continuous_kw * 0.9:
        out.append(
            f"功率贴限：Pc={plan.pc:.2f} kW 占连续功率 {plan.pc / MACHINE.power_continuous_kw * 100:.1f}%，"
            "切削中禁止再提高 fz"
        )
    margin = (request.ra_req - plan.ra_pred) / request.ra_req
    if margin < 0.2:
        out.append(
            f"粗糙度余量仅 {margin * 100:.1f}%（预测 {plan.ra_pred:.3f} μm / 要求 {request.ra_req:g} μm），"
            "首件须三点实测"
        )
    if material.requires_coolant and not request.coolant_available:
        out.append(f"{material.name} 必须内冷或高压外冷，当前不具备，不得开切")
    if tool.overhang_ratio > 4.0:
        out.append(
            f"悬伸比 {tool.overhang_ratio:.2f} > 4.0，切深已折减，严禁改用更长刀杆"
        )
    if request.wall_thickness is not None and request.wall_thickness < 3.0:
        out.append(
            f"薄壁件（壁厚 {request.wall_thickness:g} mm），切深已折减 50%，须增加辅助支撑"
        )
    if report is not None and report.iterations > 0:
        out.append(
            f"原始参数经 {report.iterations} 轮收敛后才通过硬约束，工艺卡须标注收敛动作"
        )
    return out


def _build_rationality(
    plan: Plan, request: RecommendationRequest, material: Material, tool: Tool
) -> str:
    """构造参数合理性说明，含完整代入过程。"""
    lines = [
        f"材料 {material.code.value}（{material.name}，Kc={material.kc:g} N/mm²，"
        f"Taylor C={material.taylor_c:g}、n={material.taylor_n:g}，K_ra={material.k_ra:g}）；",
        f"刀具 {tool.code.value}（{tool.kind}，D={tool.diameter:g} mm，z={tool.teeth}，"
        f"rε={tool.corner_radius:g} mm，L/D={tool.overhang_ratio:.2f}）；",
        f"n = 1000 × vc / (π × D) = 1000 × {plan.vc:g} / (π × {tool.diameter:g}) = {plan.n:.1f} r/min；",
        f"vf = fz × z × n = {plan.fz:g} × {tool.teeth} × {plan.n:.1f} = {plan.vf:.1f} mm/min；",
        f"MRR = ap × ae × vf = {plan.ap:g} × {plan.ae:g} × {plan.vf:.1f} = {plan.mrr:.0f} mm³/min；",
        f"Pc = MRR × Kc / (60×10⁶) = {plan.mrr:.0f} × {material.kc:g} / 6×10⁷ = {plan.pc:.3f} kW；",
        (
            f"Ra_th = fz² / (8 × R) × 1000 = {plan.fz:g}² / (8 × {tool.corner_radius:g}) × 1000 "
            f"= {plan.ra_th:.4f} μm；"
            if tool.is_ball
            else f"Ra_th = fz² / (32 × rε) × 1000 = {plan.fz:g}² / (32 × {tool.corner_radius:g}) × 1000 "
            f"= {plan.ra_th:.4f} μm；"
        ),
        f"Ra_pred = Ra_th × K_ra = {plan.ra_th:.4f} × {material.k_ra:g} = {plan.ra_pred:.3f} μm；",
        f"T = (C / vc)^(1/n) = ({material.taylor_c:g} / {plan.vc:g})^(1/{material.taylor_n:g}) "
        f"= {plan.tool_life:.2f} min。",
    ]
    return "".join(lines)
