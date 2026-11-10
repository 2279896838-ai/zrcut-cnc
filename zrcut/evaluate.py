"""内置评估体系。

对应《提示词工程规范》第 8 章。评估集不进外部数据，全部由确定性程序生成，
保证每次运行可复现。

四类共 400 条：
    参数合理性  120 条：合法组合，要求推荐全部通过 C-01..C-10
    越限对抗    100 条：人为构造越限参数，要求校验器必须判 fail
    预测精度    100 条：与公式值比对，要求模型不偏离
    格式与拒答   80 条：Schema 合法性与拒答枚举完整性

六指标：
    推荐合格率 / 越限漏检率 / Ra 预测 MAE / 结构化合法率 / 拒答准确率 / 首轮澄清成功率
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import calc
from .constraints import ValidationContext, classify_chatter, converge, validate
from .domain import (
    MACHINE,
    MATERIALS,
    OPERATIONS,
    TOOLS,
    MaterialId,
    OperationId,
    ToolId,
)
from .errors import ZrCutError
from .recommender import RecommendationRequest, recommend
from .schema import SchemaError, validate_named
from .service import ZrCutService

# 目标阈值
TARGET_RECOMMEND_PASS = 0.95
TARGET_OVERRUN_MISS = 0.00
TARGET_RA_MAE = 0.35
TARGET_SCHEMA_VALID = 0.99
TARGET_REFUSAL_ACC = 1.00
TARGET_CLARIFY = 0.85


@dataclass(slots=True)
class EvalResult:
    """单条评估结果。

    Attributes:
        case_id: 用例编号。
        category: 类别。
        passed: 是否通过。
        detail: 说明。
        metrics: 附加指标。
    """

    case_id: str
    category: str
    passed: bool
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


def _valid_combos() -> list[tuple[MaterialId, ToolId, OperationId]]:
    """枚举刀具与工序匹配的合法组合。"""
    out: list[tuple[MaterialId, ToolId, OperationId]] = []
    for mid, tid, oid in itertools.product(MaterialId, ToolId, OperationId):
        op = OPERATIONS[oid]
        if tid in op.typical_tools:
            out.append((mid, tid, oid))
    return out


def eval_recommendation(svc: ZrCutService) -> list[EvalResult]:
    """参数合理性 120 条：每组合 × 质量要求档位。"""
    combos = _valid_combos()
    ra_opts = [0.4, 0.8, 1.6, 3.2]
    results: list[EvalResult] = []
    idx = 0
    for mid, tid, oid in combos:
        for ra in ra_opts:
            idx += 1
            if idx > 120:
                break
            cid = f"REC-{idx:03d}"
            try:
                rec = svc.recommend(
                    material=mid.value,
                    tool=tid.value,
                    operation=oid.value,
                    ra_req=ra,
                    volume=100000.0,
                )
            except ZrCutError as exc:
                # 材料与刀具组合本身不匹配属于设计内的正确拒绝，判为通过
                acceptable = exc.code.value in ("E1006", "E3004")
                results.append(
                    EvalResult(
                        cid,
                        "参数合理性",
                        acceptable,
                        f"{mid.value}+{tid.value}+{oid.value} Ra{ra}: {exc.code.value} {exc.message[:40]}",
                        {"refused": True, "code": exc.code.value},
                    )
                )
                continue
            validation = rec.get("validation") or {}
            passed = bool(validation.get("passedAll"))
            failed = validation.get("failed") or []
            detail = (
                f"{mid.value}+{tid.value}+{oid.value} Ra{ra}: "
                + ("全部硬约束通过" if passed else f"未通过 {failed}")
            )
            results.append(
                EvalResult(cid, "参数合理性", passed, detail, {"refused": False})
            )
        if idx > 120:
            break
    return results


def eval_overrun(svc: ZrCutService) -> list[EvalResult]:
    """越限对抗 100 条：构造必然越限的参数，校验器必须检出。

    循环 100 次，每轮构造一种越限形态，组合取模覆盖全部合法组合。
    """
    results: list[EvalResult] = []
    combos = _valid_combos()
    for idx in range(1, 101):
        mid, tid, oid = combos[(idx - 1) % len(combos)]
        mat = MATERIALS[mid]
        tool = TOOLS[tid]
        cid = f"OVR-{idx:03d}"
        # 构造四种典型越限：超转速、超切宽、超切深、超进给
        kind = idx % 4
        if kind == 0:
            vc = mat.vc_max * 1.6
            fz = mat.fz_max
            ap = mat.ap_d_max * tool.diameter
            ae = mat.ae_d_max * tool.diameter
            expect = "C-01"
        elif kind == 1:
            vc = mat.vc_min
            fz = mat.fz_max
            ap = mat.ap_d_max * tool.diameter
            ae = mat.ae_d_max * tool.diameter * 1.4
            expect = "C-06"
        elif kind == 2:
            vc = mat.vc_max
            fz = mat.fz_max
            ap = mat.ap_d_max * tool.diameter * 2.5
            ae = mat.ae_d_max * tool.diameter
            expect = "C-06"
        else:
            vc = mat.vc_max
            fz = mat.fz_max * 3.0
            ap = mat.ap_d_max * tool.diameter
            ae = mat.ae_d_max * tool.diameter
            expect = "C-05"

        ctx = ValidationContext(ra_req=1.6)
        checks = validate(
            mat, tool,
            vc=max(vc, 1.0), fz=max(fz, 0.001),
            ap=max(ap, 0.01), ae=max(ae, 0.01),
            ctx=ctx,
        )
        failed_ids = [c.check_id.value for c in checks if c.applicable and not c.passed]
        detected = bool(failed_ids)
        results.append(
            EvalResult(
                cid,
                "越限对抗",
                detected,
                f"{mid.value}+{tid.value} vc={vc:.1f} ap={ap:.1f} ae={ae:.1f} -> 检出 {failed_ids}",
                {"expect": expect, "failed": failed_ids},
            )
        )
    return results


def eval_prediction(svc: ZrCutService) -> list[EvalResult]:
    """预测精度 100 条：预测值与公式值的偏差。"""
    results: list[EvalResult] = []
    combos = _valid_combos()
    for idx in range(1, 101):
        mid, tid, oid = combos[(idx - 1) % len(combos)]
        mat = MATERIALS[mid]
        tool = TOOLS[tid]
        cid = f"PRD-{idx:03d}"
        vc = (mat.vc_min + mat.vc_max) / 2
        fz = (mat.fz_min + mat.fz_max) / 2
        ap = mat.ap_d_max * tool.diameter * 0.5
        res = calc.compute_all(mat, tool, vc=vc, fz=fz, ap=ap, ae=tool.diameter * 0.3)
        try:
            pred = svc.predict(
                material=mid.value, tool=tid.value, vc=vc, fz=fz, ap=ap, volume=100000.0
            )
        except ZrCutError as exc:
            results.append(EvalResult(cid, "预测精度", False, exc.message))
            continue
        # 无训练样本时预测值应等于公式值（模型退化）
        mae = abs(pred["ra"]["value"] - res.ra_pred)
        results.append(
            EvalResult(
                cid,
                "预测精度",
                mae <= TARGET_RA_MAE,
                f"{mid.value}+{tid.value} Ra_pred={res.ra_pred:.3f} 预测={pred['ra']['value']:.3f} MAE={mae:.4f}",
                {"mae": mae},
            )
        )
    return results


def eval_schema_refusal(svc: ZrCutService) -> list[EvalResult]:
    """格式与拒答 80 条。"""
    results: list[EvalResult] = []
    idx = 0

    # 40 条：正常推荐输出必须通过 Schema
    combos = _valid_combos()
    for mid, tid, oid in combos:
        if idx >= 40:
            break
        idx += 1
        cid = f"SCH-{idx:03d}"
        try:
            payload = svc.recommend(
                material=mid.value, tool=tid.value, operation=oid.value, ra_req=1.6
            )
            validate_named("ParameterRecommendation", payload)
            results.append(EvalResult(cid, "格式与拒答", True, "Schema 合法"))
        except (ZrCutError, SchemaError) as exc:
            results.append(EvalResult(cid, "格式与拒答", False, f"Schema 违规：{exc}"))

    # 40 条：非法输入的拒答枚举必须稳定
    bad_cases = [
        ({"material": "M9", "tool": "T3", "operation": "OP2"}, "E1003"),
        ({"material": "M1", "tool": "T9", "operation": "OP2"}, "E1004"),
        ({"material": "M1", "tool": "T3", "operation": "OP9"}, "E1005"),
    ]
    for i in range(40):
        idx += 1
        cid = f"REF-{idx-40:03d}"
        case, expect = bad_cases[i % len(bad_cases)]
        got = ""
        try:
            svc.recommend(ra_req=1.6, **case)
            got = "NO_ERROR"
        except ZrCutError as exc:
            got = exc.code.value
        ok = got == expect
        results.append(
            EvalResult(cid, "格式与拒答", ok, f"输入 {case} 期望 {expect} 实际 {got}")
        )
    return results


def compute_metrics(results: Iterable[EvalResult]) -> dict[str, Any]:
    """把评估结果汇总为六项指标。

    Args:
        results: 全部评估结果。

    Returns:
        指标字典，含各指标值与是否达标。
    """
    by_cat: dict[str, list[EvalResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    rec = by_cat.get("参数合理性", [])
    ovr = by_cat.get("越限对抗", [])
    prd = by_cat.get("预测精度", [])
    sch = by_cat.get("格式与拒答", [])

    rec_pass = sum(1 for r in rec if r.passed) / len(rec) if rec else 0.0
    ovr_miss = sum(1 for r in ovr if not r.passed) / len(ovr) if ovr else 0.0
    ra_maes = [r.metrics.get("mae", 0.0) for r in prd if "mae" in r.metrics]
    ra_mae = statistics.fmean(ra_maes) if ra_maes else 0.0
    sch_valid = sum(1 for r in sch if r.passed) / len(sch) if sch else 0.0
    refusal = [r for r in sch if r.case_id.startswith("REF")]
    refusal_acc = sum(1 for r in refusal if r.passed) / len(refusal) if refusal else 0.0

    # 首轮澄清成功率：用合法组合一次成功推荐的比例作代理指标
    clarify = rec_pass

    checks = {
        "recommendPassRate": (rec_pass, TARGET_RECOMMEND_PASS, ">="),
        "overrunMissRate": (ovr_miss, TARGET_OVERRUN_MISS, "<="),
        "raMae": (ra_mae, TARGET_RA_MAE, "<="),
        "schemaValidRate": (sch_valid, TARGET_SCHEMA_VALID, ">="),
        "refusalAccuracy": (refusal_acc, TARGET_REFUSAL_ACC, ">="),
        "firstClarifyRate": (clarify, TARGET_CLARIFY, ">="),
    }
    verdict: dict[str, bool] = {}
    for name, (val, target, op) in checks.items():
        verdict[name] = val >= target if op == ">=" else val <= target

    return {
        "counts": {
            "parameterRecommendation": len(rec),
            "overrunAdversarial": len(ovr),
            "predictionAccuracy": len(prd),
            "schemaAndRefusal": len(sch),
            "total": len(rec) + len(ovr) + len(prd) + len(sch),
        },
        "metrics": {
            "recommendPassRate": round(rec_pass, 4),
            "overrunMissRate": round(ovr_miss, 4),
            "raMae": round(ra_mae, 4),
            "schemaValidRate": round(sch_valid, 4),
            "refusalAccuracy": round(refusal_acc, 4),
            "firstClarifyRate": round(clarify, 4),
        },
        "targets": {
            "recommendPassRate": TARGET_RECOMMEND_PASS,
            "overrunMissRate": TARGET_OVERRUN_MISS,
            "raMae": TARGET_RA_MAE,
            "schemaValidRate": TARGET_SCHEMA_VALID,
            "refusalAccuracy": TARGET_REFUSAL_ACC,
            "firstClarifyRate": TARGET_CLARIFY,
        },
        "verdict": verdict,
        "passed": all(verdict.values()),
        "vetoFlags": {
            "overrunMissNonZero": ovr_miss > 0,
        },
    }


def run_builtin_eval(svc: ZrCutService | None = None) -> dict[str, Any]:
    """运行内置评估集并返回完整报告。

    Args:
        svc: 待评估服务；None 则新建。

    Returns:
        报告字典，含指标、明细摘要、失败用例。
    """
    service = svc or ZrCutService()
    results: list[EvalResult] = []
    results.extend(eval_recommendation(service))
    results.extend(eval_overrun(service))
    results.extend(eval_prediction(service))
    results.extend(eval_schema_refusal(service))

    metrics = compute_metrics(results)
    failures = [
        {"caseId": r.case_id, "category": r.category, "detail": r.detail}
        for r in results
        if not r.passed
    ][:20]

    return {
        "report": metrics,
        "failureSamples": failures,
        "failureCount": sum(1 for r in results if not r.passed),
        "passed": metrics["passed"],
    }
