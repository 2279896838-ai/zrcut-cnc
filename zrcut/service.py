"""服务编排层：把各模块组装为业务用例。

这是系统的唯一业务入口。HTTP API、CLI、评估脚本都只调用本模块，
保证三条入口的行为完全一致。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import calc
from .card import CardStatus, ProcessCard, build_card, next_card_id
from .closure import ClosureAssessment, ClosureRecord, ClosureStore
from .constraints import CheckResult, ValidationContext, ValidationReport, converge, validate
from .domain import (
    MACHINE,
    Confidence,
    MaterialId,
    OperationId,
    RefusalReason,
    Strategy,
    ToolId,
    get_material,
    get_operation,
    get_tool,
)
from .errors import ErrorCode, ZrCutError, no_feasible_plan, unknown_material, unknown_operation, unknown_tool
from .knowledge import KnowledgeBase, SearchHit, default_knowledge_base
from .llm import LLMClient, check_numeric_discipline, load_prompt, render_template
from .predictor import FEATURE_NAMES, Predictor, PredictorError
from .recommender import (
    CoolantRequiredError,
    Plan,
    Recommendation,
    RecommendationError,
    RecommendationRequest,
    RoughnessUnachievableError,
    ToolOperationMismatchError,
    recommend,
)
from .schema import validate_named

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceConfig:
    """服务配置。

    Attributes:
        enable_llm: 是否启用 LLM 叙述。
        strict_schema: 是否强制 Schema 校验失败即报错。
        enable_numeric_check: 是否启用数值纪律检查。
        default_ra_req: 默认粗糙度要求。
    """

    enable_llm: bool = False
    strict_schema: bool = True
    enable_numeric_check: bool = True
    default_ra_req: float = 3.2


class ZrCutService:
    """智刃工艺服务门面。

    Attributes:
        config: 配置。
        kb: 知识库。
        closure_store: 回填仓库。
        predictor: 预测器。
        llm: LLM 客户端。
        cards: 工艺卡内存仓库。
    """

    def __init__(
        self,
        *,
        config: ServiceConfig | None = None,
        kb: KnowledgeBase | None = None,
        closure_store: ClosureStore | None = None,
        predictor: Predictor | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.config = config or ServiceConfig()
        self.kb = kb if kb is not None else default_knowledge_base()
        self.closure_store = closure_store or ClosureStore()
        self.predictor = predictor or Predictor()
        self.llm = llm or LLMClient()
        self.cards: dict[str, ProcessCard] = {}

    # ---------------------------------------------------------------- 推荐
    def recommend(
        self,
        *,
        material: str,
        tool: str,
        operation: str,
        ra_req: float | None = None,
        strategy: str | None = None,
        volume: float | None = None,
        wall_thickness: float | None = None,
        coolant_available: bool = True,
        use_llm: bool | None = None,
        role: str = "process_engineer",
    ) -> dict[str, Any]:
        """执行参数推荐全流程。

        Args:
            material: 材料代号 M1-M5。
            tool: 刀具代号 T1-T6。
            operation: 工序代号 OP1-OP4。
            ra_req: 粗糙度要求，μm。
            strategy: 策略代号。
            volume: 待去除体积，mm³。
            wall_thickness: 壁厚，mm。
            coolant_available: 冷却是否具备。
            use_llm: 是否用 LLM 叙述；None 取配置。
            role: 调用角色。

        Returns:
            含 recommendation、validation、knowledge、narrative 等字段的字典。

        Raises:
            ZrCutError: 输入非法或无可行方案。
        """
        mat_obj = self._parse_material(material)
        tl_obj = self._parse_tool(tool)
        op = self._parse_operation(operation)

        strat: Strategy | None = None
        if strategy:
            try:
                strat = Strategy(strategy.upper())
            except ValueError as exc:
                raise ZrCutError(
                    ErrorCode.INVALID_INPUT,
                    f"未知策略：{strategy}；须为 EFFICIENCY/QUALITY/LIFE/BALANCED",
                    {"strategy": strategy},
                ) from exc

        req = RecommendationRequest(
            material=mat_obj.code,
            tool=tl_obj.code,
            operation=op,
            ra_req=ra_req if ra_req is not None else self.config.default_ra_req,
            strategy=strat,
            volume=volume,
            wall_thickness=wall_thickness,
            coolant_available=coolant_available,
        )

        if self.closure_store.is_frozen(mat_obj.code.value, tl_obj.code.value):
            raise ZrCutError(
                ErrorCode.CONSTRAINT_VIOLATED,
                f"材料 {mat_obj.code.value} + 刀具 {tl_obj.code.value} 组合因回填偏差严重已被冻结，"
                "须工艺工程师复核解冻后方可自动推荐",
                {"material": mat_obj.code.value, "tool": tl_obj.code.value},
                409,
            )

        try:
            recommendation = recommend(req)
        except CoolantRequiredError as exc:
            raise ZrCutError(
                ErrorCode.COOLANT_REQUIRED,
                str(exc),
                {
                    "material": mat_obj.code.value,
                    "coolantAvailable": coolant_available,
                    "safeAction": "停机，接入内冷或高压外冷后重新推荐",
                },
                422,
            ) from exc
        except ToolOperationMismatchError as exc:
            raise ZrCutError(
                ErrorCode.TOOL_OPERATION_MISMATCH,
                str(exc),
                {
                    "tool": tl_obj.code.value,
                    "operation": op.value,
                },
                422,
            ) from exc
        except RoughnessUnachievableError as exc:
            raise ZrCutError(
                ErrorCode.ROUGHNESS_UNACHIEVABLE,
                str(exc),
                {
                    "material": mat_obj.code.value,
                    "tool": tl_obj.code.value,
                    "raReq": req.ra_req,
                },
                422,
            ) from exc
        except RecommendationError as exc:
            raise ZrCutError(
                ErrorCode.NO_FEASIBLE_PLAN,
                str(exc),
                {
                    "material": mat_obj.code.value,
                    "tool": tl_obj.code.value,
                    "operation": op.value,
                    "raReq": req.ra_req,
                },
                422,
            ) from exc
        payload = recommendation.to_dict()
        if self.config.strict_schema:
            validate_named("ParameterRecommendation", payload)

        hits = self._search_for(mat_obj.code, tl_obj.code, op)
        payload["knowledge"] = [h.to_dict() for h in hits]
        payload["conflicts"] = self.kb.detect_conflicts(hits)

        narrative, mode = self._narrate(
            recommendation=recommendation,
            hits=hits,
            use_llm=use_llm,
            role=role,
        )
        payload["narrative"] = narrative
        payload["narrativeMode"] = mode
        return payload

    # ---------------------------------------------------------------- 校验
    def validate_params(
        self,
        *,
        material: str,
        tool: str,
        vc: float,
        fz: float,
        ap: float,
        ae: float,
        ra_req: float | None = None,
        wall_thickness: float | None = None,
        coolant_available: bool = True,
        converge_attempt: bool = True,
    ) -> dict[str, Any]:
        """对给定参数执行十条硬约束校验，可选自动收敛。

        Args:
            material: 材料代号。
            tool: 刀具代号。
            vc: 切削速度，m/min。
            fz: 每齿进给，mm/z。
            ap: 轴向切深，mm。
            ae: 径向切宽，mm。
            ra_req: 粗糙度要求。
            wall_thickness: 壁厚。
            coolant_available: 冷却是否具备。
            converge_attempt: 是否尝试收敛。

        Returns:
            含 results、converged、finalParams 的字典。
        """
        mat = self._parse_material(material)
        tl = self._parse_tool(tool)
        ctx = ValidationContext(
            ra_req=ra_req if ra_req is not None else self.config.default_ra_req,
            wall_thickness=wall_thickness,
            coolant_available=coolant_available,
        )
        for name, val in (("vc", vc), ("fz", fz), ("ap", ap), ("ae", ae)):
            if val <= 0:
                raise ZrCutError(
                    ErrorCode.INVALID_INPUT,
                    f"{name} 必须为正，当前 {val}",
                    {"field": name, "value": val},
                )

        if converge_attempt:
            report = converge(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae, ctx=ctx)
            out = report.to_dict()
            p = report.final_params
            res = calc.compute_all(mat, tl, vc=p["vc"], fz=p["fz"], ap=p["ap"], ae=p["ae"])
            out["finalMetrics"] = {
                "n": round(res.n, 1),
                "vf": round(res.vf, 1),
                "mrr": round(res.mrr, 0),
                "pc": round(res.pc, 3),
                "raPred": round(res.ra_pred, 3),
                "toolLife": round(res.tool_life, 2),
            }
        else:
            results = validate(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae, ctx=ctx)
            report = ValidationReport(
                results=results,
                converged=not [r for r in results if r.applicable and not r.passed],
                iterations=0,
                final_params={"vc": vc, "fz": fz, "ap": ap, "ae": ae},
            )
            out = report.to_dict()
            res = calc.compute_all(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae)
            out["finalMetrics"] = {
                "n": round(res.n, 1),
                "vf": round(res.vf, 1),
                "mrr": round(res.mrr, 0),
                "pc": round(res.pc, 3),
                "raPred": round(res.ra_pred, 3),
                "toolLife": round(res.tool_life, 2),
            }

        if self.config.strict_schema:
            validate_named("ValidationReport", out)
        return out

    # ---------------------------------------------------------------- 预测
    def predict(
        self,
        *,
        material: str,
        tool: str,
        vc: float,
        fz: float,
        ap: float,
        volume: float | None = None,
    ) -> dict[str, Any]:
        """给出三项预测及置信区间。

        Args:
            material: 材料代号。
            tool: 刀具代号。
            vc: 切削速度。
            fz: 每齿进给。
            ap: 轴向切深。
            volume: 待去除体积。

        Returns:
            含 ra、life、time 三个区间的字典。
        """
        mat = self._parse_material(material)
        tl = self._parse_tool(tool)
        res = calc.compute_all(mat, tl, vc=vc, fz=fz, ap=ap, ae=mat.ae_d_max * tl.diameter)
        tm_theory = (
            calc.machining_time_for(volume, res) if volume is not None else res.tool_life
        )
        features = [
            float(mat.hardness.split("-")[0].replace("HB ", "").strip() or 200),
            mat.kc,
            tl.diameter,
            float(tl.teeth),
            tl.corner_radius,
            vc,
            fz,
            ap,
        ]
        try:
            intervals = self.predictor.predict(
                features,
                ra_theory=res.ra_th,
                life_theory=res.tool_life,
                time_theory=tm_theory,
            )
        except PredictorError as exc:
            raise ZrCutError(
                ErrorCode.CALCULATION_FAILED, f"预测失败：{exc}", {}, 422
            ) from exc

        out = {
            "metrics": {
                "n": round(res.n, 1),
                "vf": round(res.vf, 1),
                "mrr": round(res.mrr, 0),
                "pc": round(res.pc, 3),
                "raTh": round(res.ra_th, 4),
            },
            "ra": intervals["ra"].to_dict(),
            "life": intervals["life"].to_dict(),
            "time": intervals["time"].to_dict(),
        }
        # 数值纪律：模型偏离公式超 40% 必须告警
        dev = abs(intervals["ra"].value - res.ra_pred) / res.ra_pred if res.ra_pred else 0
        if dev > 0.40:
            out["warning"] = (
                f"模型预测 Ra {intervals['ra'].value:.3f} μm 与公式值 {res.ra_pred:.3f} μm "
                f"偏离 {dev * 100:.1f}%，超过 40% 告警阈值，请复核该材料+刀具组合的样本质量"
            )
        return out

    # ---------------------------------------------------------------- 工艺卡
    def create_card(
        self,
        *,
        part_name: str,
        blank_size: str,
        op_no: int = 10,
        material: str | None = None,
        tool: str | None = None,
        operation: str | None = None,
        ra_req: float | None = None,
        volume: float | None = None,
        wall_thickness: float | None = None,
        coolant_available: bool = True,
        card_id: str | None = None,
    ) -> dict[str, Any]:
        """生成工艺卡（草稿态）。

        Args:
            part_name: 零件名。
            blank_size: 毛坯尺寸。
            op_no: 工序号。
            material: 材料代号。
            tool: 刀具代号。
            operation: 工序代号。
            ra_req: 粗糙度要求。
            volume: 去除体积。
            wall_thickness: 壁厚。
            coolant_available: 冷却是否具备。
            card_id: 指定卡号。

        Returns:
            工艺卡字典。
        """
        if not material or not tool or not operation:
            raise ZrCutError(
                ErrorCode.MISSING_SLOT,
                "生成工艺卡必须提供 material、tool、operation",
                {
                    "missing": [
                        k
                        for k, v in (
                            ("material", material),
                            ("tool", tool),
                            ("operation", operation),
                        )
                        if not v
                    ]
                },
            )
        rec_payload = self.recommend(
            material=material,
            tool=tool,
            operation=operation,
            ra_req=ra_req,
            volume=volume,
            wall_thickness=wall_thickness,
            coolant_available=coolant_available,
            use_llm=False,
        )
        mat = self._parse_material(material)
        tl = self._parse_tool(tool)
        req = RecommendationRequest(
            material=mat.code,
            tool=tl.code,
            operation=self._parse_operation(operation),
            ra_req=ra_req if ra_req is not None else self.config.default_ra_req,
            volume=volume,
            wall_thickness=wall_thickness,
            coolant_available=coolant_available,
        )
        try:
            rec = recommend(req)
        except RecommendationError as exc:
            raise ZrCutError(
                ErrorCode.NO_FEASIBLE_PLAN,
                str(exc),
                {"material": mat.code.value, "tool": tl.code.value},
                422,
            ) from exc
        cid = card_id or next_card_id()
        if cid in self.cards:
            raise ZrCutError(
                ErrorCode.CARD_INVALID, f"工艺卡号已存在：{cid}", {"cardId": cid}
            )
        card = build_card(rec, part_name=part_name, blank_size=blank_size, op_no=op_no, card_id=cid)
        self.cards[card.card_id] = card
        out = card.to_dict()
        out["markdown"] = card.to_markdown()
        out["qrPayload"] = card.qr_payload()
        if self.config.strict_schema:
            validate_named("ProcessCard", card.to_dict())
        logger.info("工艺卡已生成 cardId=%s", card.card_id)
        return out

    def issue_card(self, card_id: str, *, issued_by: str) -> dict[str, Any]:
        """签发并锁定工艺卡。

        Args:
            card_id: 卡号。
            issued_by: 签发人。

        Returns:
            更新后的工艺卡字典。
        """
        card = self.cards.get(card_id)
        if card is None:
            raise ZrCutError(
                ErrorCode.CARD_NOT_FOUND, f"工艺卡不存在：{card_id}", {"cardId": card_id}, 404
            )
        card.issue(issued_by)
        logger.info("工艺卡已签发 cardId=%s by=%s", card_id, issued_by)
        return card.to_dict()

    def get_card(self, card_id: str) -> dict[str, Any]:
        """读取工艺卡。

        Args:
            card_id: 卡号。

        Returns:
            工艺卡字典。
        """
        card = self.cards.get(card_id)
        if card is None:
            raise ZrCutError(
                ErrorCode.CARD_NOT_FOUND, f"工艺卡不存在：{card_id}", {"cardId": card_id}, 404
            )
        out = card.to_dict()
        out["markdown"] = card.to_markdown()
        out["qrPayload"] = card.qr_payload()
        return out

    # ---------------------------------------------------------------- 回填
    def record_closure(
        self,
        *,
        trace_id: str,
        card_id: str,
        material: str,
        tool: str,
        actual_ra: float | None = None,
        actual_time: float | None = None,
        actual_life: float | None = None,
        predicted_ra: float | None = None,
        predicted_time: float | None = None,
        predicted_life: float | None = None,
        tool_wear_vb: float | None = None,
        chatter_observed: bool = False,
        anomaly: str = "",
        operator_note: str = "",
        recorded_by: str = "",
    ) -> dict[str, Any]:
        """写入回填记录并返回偏差评估。

        若未提供预测值且工艺卡存在，则从工艺卡自动取预测值。

        Args:
            trace_id: 试切记录号。
            card_id: 关联卡号。
            material: 材料代号。
            tool: 刀具代号。
            actual_ra: 实测粗糙度。
            actual_time: 实测工时。
            actual_life: 实测寿命。
            predicted_ra: 预测粗糙度。
            predicted_time: 预测工时。
            predicted_life: 预测寿命。
            tool_wear_vb: 刀具磨损 VB。
            chatter_observed: 是否观察到颤振。
            anomaly: 异常描述。
            operator_note: 备注。
            recorded_by: 记录人。

        Returns:
            含 assessment、stats、retrain 的字典。
        """
        mat = self._parse_material(material)
        tl = self._parse_tool(tool)

        card = self.cards.get(card_id)
        if predicted_ra is None and card is not None:
            predicted_ra = card.est_roughness
        if predicted_time is None and card is not None:
            predicted_time = card.est_machining_time

        record = ClosureRecord(
            trace_id=trace_id,
            card_id=card_id,
            material_code=mat.code.value,
            tool_code=tl.code.value,
            predicted_ra=predicted_ra,
            actual_ra=actual_ra,
            predicted_time=predicted_time,
            actual_time=actual_time,
            predicted_life=predicted_life,
            actual_life=actual_life,
            tool_wear_vb=tool_wear_vb,
            chatter_observed=chatter_observed,
            anomaly=anomaly,
            operator_note=operator_note,
            recorded_by=recorded_by,
        )
        if self.config.strict_schema:
            validate_named("ClosureRecord", record.to_dict())

        assessment = self.closure_store.add(record)
        out = {
            "record": record.to_dict(),
            "assessment": assessment.to_dict(),
            "stats": self.closure_store.stats(mat.code.value, tl.code.value),
            "retrain": __import__("zrcut.closure", fromlist=["retrain"]).retrain(
                self.closure_store, material_code=mat.code.value, tool_code=tl.code.value
            ),
        }
        logger.info(
            "回填已记录 traceId=%s level=%s", trace_id, assessment.level.value
        )
        return out

    def closure_stats(
        self, *, material: str | None = None, tool: str | None = None
    ) -> dict[str, Any]:
        """查询回填聚合统计。

        Args:
            material: 材料代号过滤。
            tool: 刀具代号过滤。

        Returns:
            统计字典。
        """
        from .closure import retrain as _retrain

        stats = self.closure_store.stats(material, tool)
        if material and tool:
            stats["retrain"] = _retrain(
                self.closure_store, material_code=material, tool_code=tool
            )
            stats["frozen"] = self.closure_store.is_frozen(material, tool)
        return stats

    # ---------------------------------------------------------------- 检索
    def search(self, query: str, *, top_k: int = 5) -> dict[str, Any]:
        """检索知识库。

        Args:
            query: 查询文本。
            top_k: 条数上限。

        Returns:
            含 hits、conflicts、empty 的字典。
        """
        hits = self.kb.search(query, top_k=top_k)
        return {
            "query": query,
            "hits": [h.to_dict() for h in hits],
            "conflicts": self.kb.detect_conflicts(hits),
            "empty": len(hits) == 0,
            "emptyMessage": (
                "" if hits else "知识库未检索到相关条目，不得给出无依据结论，请转工艺工程师"
            ),
        }

    # ---------------------------------------------------------------- 内部
    def _parse_material_id(self, code: str) -> MaterialId:
        """解析材料代号为枚举，非法时抛业务异常。"""
        try:
            return MaterialId(str(code).upper())
        except (ValueError, KeyError) as exc:
            raise unknown_material(str(code)) from exc

    def _parse_tool_id(self, code: str) -> ToolId:
        """解析刀具代号为枚举，非法时抛业务异常。"""
        try:
            return ToolId(str(code).upper())
        except (ValueError, KeyError) as exc:
            raise unknown_tool(str(code)) from exc

    def _parse_operation_id(self, code: str) -> OperationId:
        """解析工序代号为枚举，非法时抛业务异常。"""
        try:
            return OperationId(str(code).upper())
        except (ValueError, KeyError) as exc:
            raise unknown_operation(str(code)) from exc

    def _parse_material(self, code: str) -> "Material":
        """解析材料代号为领域对象。

        Args:
            code: 材料代号。

        Returns:
            Material 实例。

        Raises:
            ZrCutError: 代号未登记。
        """
        return get_material(self._parse_material_id(code))

    def _parse_tool(self, code: str) -> "Tool":
        """解析刀具代号为领域对象。

        Args:
            code: 刀具代号。

        Returns:
            Tool 实例。

        Raises:
            ZrCutError: 代号未登记。
        """
        return get_tool(self._parse_tool_id(code))

    def _parse_operation(self, code: str) -> OperationId:
        """解析工序代号。

        Args:
            code: 工序代号。

        Returns:
            OperationId 枚举。

        Raises:
            ZrCutError: 代号未登记。
        """
        return self._parse_operation_id(code)

    def _search_for(
        self, mat: MaterialId, tl: ToolId, op: OperationId
    ) -> list[SearchHit]:
        """按材料刀具工序构造检索查询。"""
        m = get_material(mat)
        t = get_tool(tl)
        o = get_operation(op)
        query = f"{mat.value} {m.name} {m.grade} {t.kind} 切削速度 每齿进给 {o.name}"
        hits = self.kb.search(query)
        if not hits:
            hits = self.kb.search(f"{m.name} 切削参数")
        return hits

    def _narrate(
        self,
        *,
        recommendation: Recommendation,
        hits: Sequence[SearchHit],
        use_llm: bool | None,
        role: str,
    ) -> tuple[str, str]:
        """生成自然语言叙述，LLM 不可用时退回模板。

        Args:
            recommendation: 推荐结果。
            hits: 检索命中。
            use_llm: 是否使用 LLM。
            role: 角色。

        Returns:
            (叙述文本, 模式)。
        """
        want_llm = self.config.enable_llm if use_llm is None else use_llm
        fallback = self._template_narrative(recommendation, hits)

        if not want_llm:
            return fallback, "template"

        try:
            system_tpl = load_prompt("ZRCNC-SYS-001")
        except ZrCutError:
            return fallback, "template"

        plan = recommendation.primary
        tool_results = {
            "primary": plan.to_dict(),
            "alternatives": [p.to_dict() for p in recommendation.alternatives],
            "validation": (
                recommendation.validation.to_dict() if recommendation.validation else None
            ),
            "knowledge": [h.to_dict() for h in hits],
            "warnings": recommendation.warnings,
        }
        from .llm import build_user_message

        system = render_template(
            system_tpl,
            {
                "role": role,
                "session_id": "SESS-LOCAL",
                "machine_code": MACHINE.code,
                "machine_spec": f"{MACHINE.name}，主轴 {MACHINE.spindle_max_rpm:.0f} r/min，"
                f"连续功率 {MACHINE.power_continuous_kw:.0f} kW",
                "material_domain": "M1-M5",
                "tool_domain": "T1-T6",
                "op_domain": "OP1-OP4",
                "tool_results": tool_results,
                "retrieved_chunks": [h.to_dict() for h in hits],
                "user_query": "请给出切削参数推荐与依据",
            },
        )
        result = self.llm.complete(system, build_user_message(tool_results))
        if result.mode != "llm" or not result.text.strip():
            return fallback, "template"

        # 数值纪律检查
        if self.config.enable_numeric_check:
            allowed: list[float] = []
            for p in [plan, *recommendation.alternatives]:
                allowed.extend(
                    [
                        p.vc, p.fz, p.ap, p.ae, p.n, p.vf, p.mrr, p.pc,
                        p.ra_th, p.ra_pred, p.tool_life,
                    ]
                )
            violations = check_numeric_discipline(result.text, allowed)
            if violations:
                logger.warning(
                    "LLM 输出存在疑似自算数值 %s，已退回模板回答", violations[:5]
                )
                return fallback, "template-numeric-violation"

        return result.text, "llm"

    @staticmethod
    def _template_narrative(
        recommendation: Recommendation, hits: Sequence[SearchHit]
    ) -> str:
        """模板化叙述，覆盖率与 LLM 相当但不依赖外部服务。"""
        plan = recommendation.primary
        mat = recommendation.material
        tl = recommendation.tool
        assert mat is not None and tl is not None

        lines = [
            f"结论：{mat.code.value}+{tl.code.value} 推荐 n={plan.n:.0f} r/min、"
            f"vf={plan.vf:.0f} mm/min、ap={plan.ap:.2f} mm、ae={plan.ae:.2f} mm，"
            f"预测 Ra={plan.ra_pred:.3f} μm，置信度 {plan.confidence.value}。",
            "",
            "依据：",
            recommendation.rationality,
        ]
        if hits:
            lines.append(
                "知识来源：" + "、".join(f"[{h.item.kb_id}]" for h in hits[:3])
            )
        else:
            lines.append("知识来源：知识库未检索到，结论仅基于公式计算")
        if recommendation.alternatives:
            lines.append("")
            lines.append("备选方案：")
            for alt in recommendation.alternatives:
                lines.append(
                    f"- {alt.label}（{alt.strategy.value}）：vc={alt.vc:.0f} m/min、"
                    f"fz={alt.fz:.4f} mm/z、Pc={alt.pc:.2f} kW、"
                    f"T={alt.tool_life:.1f} min、Ra={alt.ra_pred:.3f} μm"
                )
        if recommendation.warnings:
            lines.append("")
            lines.append("风险：")
            lines.extend(f"- {w}" for w in recommendation.warnings)
        return "\n".join(lines)
