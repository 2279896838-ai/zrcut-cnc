#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 JS 引擎的金标准测试向量。

浏览器端引擎（web/static/engine.js）是 Python 端算法的移植版。为保证两边
数值完全一致，这里用 Python 端生成基准值，JS 端测试脚本逐项比对。

用法：
    python scripts/gen-golden-vectors.py
输出：
    web/golden-vectors.json
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zrcut import calc  # noqa: E402
from zrcut.constraints import ValidationContext, classify_chatter, converge, validate  # noqa: E402
from zrcut.domain import (  # noqa: E402
    MACHINE,
    MATERIALS,
    OPERATIONS,
    TOOLS,
    MaterialId,
    OperationId,
    ToolId,
    chatter_stable_rpm,
)
from zrcut.knowledge import default_knowledge_base, tokenize  # noqa: E402
from zrcut.recommender import RecommendationRequest, recommend  # noqa: E402

OUT = ROOT / "web" / "golden-vectors.json"


def round6(x: float) -> float:
    """统一精度，避免浮点末位差异。"""
    return round(float(x), 6)


def build_domain() -> dict:
    """导出领域常量。"""
    return {
        "machine": {
            "code": MACHINE.code,
            "name": MACHINE.name,
            "spindleMaxRpm": MACHINE.spindle_max_rpm,
            "powerContinuousKw": MACHINE.power_continuous_kw,
            "powerPeakKw": MACHINE.power_peak_kw,
            "taper": MACHINE.taper,
            "feedMin": MACHINE.feed_min_mmpm,
            "feedMax": MACHINE.feed_max_mmpm,
            "naturalFrequencyHz": MACHINE.natural_frequency_hz,
            "coolant": MACHINE.coolant_type,
            "coolantPressure": list(MACHINE.coolant_pressure_mpa),
        },
        "materials": {
            m.code.value: {
                "code": m.code.value,
                "name": m.name,
                "grade": m.grade,
                "hardness": m.hardness,
                "kc": m.kc,
                "taylorC": m.taylor_c,
                "taylorN": m.taylor_n,
                "kRa": m.k_ra,
                "vcMin": m.vc_min,
                "vcMax": m.vc_max,
                "fzMin": m.fz_min,
                "fzMax": m.fz_max,
                "aeDMax": m.ae_d_max,
                "apDMax": m.ap_d_max,
                "requiresCoolant": m.requires_coolant,
            }
            for m in MATERIALS.values()
        },
        "tools": {
            t.code.value: {
                "code": t.code.value,
                "kind": t.kind,
                "diameter": t.diameter,
                "teeth": t.teeth,
                "coating": t.coating,
                "overhang": t.overhang,
                "fluteLength": t.flute_length,
                "cornerRadius": t.corner_radius,
                "isBall": t.is_ball,
                "overhangRatio": round6(t.overhang_ratio),
            }
            for t in TOOLS.values()
        },
        "operations": {
            o.code.value: {
                "code": o.code.value,
                "name": o.name,
                "typicalTools": [x.value for x in o.typical_tools],
            }
            for o in OPERATIONS.values()
        },
    }


def build_calc_vectors() -> list[dict]:
    """公式层向量：覆盖多种材料刀具与参数组合。"""
    vectors: list[dict] = []
    combos = [
        ("M1", "T3", 168.0, 0.12, 8.0, 4.0, 288000.0),
        ("M1", "T4", 180.0, 0.12, 17.0, 10.0, 61200.0),
        ("M2", "T2", 400.0, 0.15, 12.0, 6.0, 100000.0),
        ("M2", "T1", 246.3, 0.19, 8.8, 4.9, 50000.0),
        ("M3", "T1", 80.0, 0.06, 2.0, 2.0, 30000.0),
        ("M4", "T6", 150.0, 0.12, 2.0, 40.0, 400000.0),
        ("M5", "T5", 45.0, 0.05, 2.1, 3.0, 20000.0),
        ("M5", "T5", 30.0, 0.03, 1.5, 1.5, 12000.0),
        ("M1", "T6", 100.0, 0.10, 1.5, 30.0, 200000.0),
        ("M3", "T2", 95.0, 0.07, 2.5, 2.5, 25000.0),
    ]
    for m, t, vc, fz, ap, ae, vol in combos:
        mat, tl = MATERIALS[MaterialId(m)], TOOLS[ToolId(t)]
        r = calc.compute_all(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae)
        vectors.append(
            {
                "input": {
                    "material": m, "tool": t, "vc": vc, "fz": fz,
                    "ap": ap, "ae": ae, "volume": vol,
                },
                "output": {
                    "n": round6(r.n),
                    "vf": round6(r.vf),
                    "mrr": round6(r.mrr),
                    "pc": round6(r.pc),
                    "raTh": round6(r.ra_th),
                    "raPred": round6(r.ra_pred),
                    "toolLife": round6(r.tool_life),
                    "overhangRatio": round6(r.overhang_ratio),
                    "toothPassHz": round6(r.tooth_pass_hz),
                    "machiningTime": round6(vol / r.mrr) if vol else None,
                },
            }
        )
    return vectors


def build_validation_vectors() -> list[dict]:
    """校验向量：涵盖通过、越限、不适用等情形。"""
    cases = [
        ("M1", "T3", 168.0, 0.12, 8.0, 4.0, 1.6, None, True),
        ("M1", "T4", 180.0, 0.12, 20.0, 14.0, 1.6, None, True),
        ("M2", "T1", 800.0, 0.10, 5.0, 3.0, 3.2, None, True),
        ("M3", "T1", 80.0, 0.06, 2.0, 2.0, 0.8, None, False),
        ("M1", "T3", 168.0, 0.12, 8.0, 4.0, 1.6, 2.0, True),
        ("M1", "T5", 120.0, 0.08, 3.0, 2.0, 1.6, None, True),
        ("M4", "T6", 150.0, 0.12, 2.0, 40.0, 3.2, None, True),
        ("M1", "T3", 300.0, 0.10, 8.0, 4.0, 1.6, None, True),
        ("M1", "T3", 168.0, 0.50, 8.0, 4.0, 1.6, None, True),
        ("M5", "T5", 60.0, 0.06, 3.0, 3.0, 0.4, None, True),
    ]
    vectors: list[dict] = []
    for m, t, vc, fz, ap, ae, ra, wall, coolant in cases:
        mat, tl = MATERIALS[MaterialId(m)], TOOLS[ToolId(t)]
        ctx = ValidationContext(ra_req=ra, wall_thickness=wall, coolant_available=coolant)
        results = validate(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae, ctx=ctx)
        vectors.append(
            {
                "input": {
                    "material": m, "tool": t, "vc": vc, "fz": fz,
                    "ap": ap, "ae": ae, "raReq": ra,
                    "wallThickness": wall, "coolantAvailable": coolant,
                },
                "output": [
                    {
                        "checkId": r.check_id.value,
                        "passed": r.passed,
                        "applicable": r.applicable,
                        "actual": r.actual if isinstance(r.actual, (int, float, str)) else str(r.actual),
                        "limit": r.limit if isinstance(r.limit, (int, float, str)) else str(r.limit),
                    }
                    for r in results
                ],
            }
        )
    return vectors


def build_converge_vectors() -> list[dict]:
    """收敛向量：覆盖多种越限形态。"""
    cases = [
        ("M1", "T4", 180.0, 0.12, 20.0, 14.0, 1.6),
        ("M2", "T1", 480.0, 0.19, 20.0, 7.0, 1.6),
        ("M2", "T2", 500.0, 0.20, 10.0, 6.0, 3.2),
        ("M1", "T3", 168.0, 0.12, 8.0, 4.0, 1.6),
        ("M5", "T5", 60.0, 0.06, 3.0, 3.0, 0.4),
        ("M3", "T1", 100.0, 0.08, 1.5, 1.0, 0.8),
    ]
    vectors: list[dict] = []
    for m, t, vc, fz, ap, ae, ra in cases:
        mat, tl = MATERIALS[MaterialId(m)], TOOLS[ToolId(t)]
        ctx = ValidationContext(ra_req=ra)
        rep = converge(mat, tl, vc=vc, fz=fz, ap=ap, ae=ae, ctx=ctx)
        vectors.append(
            {
                "input": {
                    "material": m, "tool": t, "vc": vc, "fz": fz,
                    "ap": ap, "ae": ae, "raReq": ra,
                },
                "output": {
                    "converged": rep.converged,
                    "finalParams": {k: round6(v) for k, v in rep.final_params.items()},
                    "iterations": rep.iterations,
                },
            }
        )
    return vectors


def build_chatter_vectors() -> list[dict]:
    """颤振分级向量。"""
    vectors: list[dict] = []
    for tid, rpm, ae, ap in [
        ("T3", 2400.0, 6.0, 8.0),
        ("T3", 3400.0, 6.0, 7.0),
        ("T4", 2864.8, 14.0, 17.0),
        ("T1", 1432.4, 0.3, 1.0),
        ("T6", 596.8, 40.0, 2.0),
        ("T5", 1432.4, 3.0, 2.1),
        ("T3", 4800.0, 7.0, 8.0),
    ]:
        tl = TOOLS[ToolId(tid)]
        risk, _why = classify_chatter(tl, rpm, ae, ap)
        vectors.append(
            {
                "input": {"tool": tid, "rpm": rpm, "ae": ae, "ap": ap},
                "output": {"risk": risk.value},
            }
        )
    return vectors


def build_recommend_vectors() -> list[dict]:
    """推荐向量：覆盖材料×刀具×工序×Ra 组合。"""
    vectors: list[dict] = []
    seen: set[tuple] = set()
    for mid, oid in itertools.product(MaterialId, OperationId):
        op = OPERATIONS[oid]
        for tid in op.typical_tools:
            for ra in (0.4, 1.6, 3.2):
                key = (mid.value, tid.value, oid.value, ra)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    rec = recommend(
                        RecommendationRequest(
                            material=mid, tool=tid, operation=oid,
                            ra_req=ra, volume=100000.0,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - 记录拒绝原因
                    vectors.append(
                        {
                            "input": {
                                "material": mid.value, "tool": tid.value,
                                "operation": oid.value, "raReq": ra,
                                "volume": 100000.0,
                            },
                            "error": type(exc).__name__,
                        }
                    )
                    continue
                p = rec.primary
                vectors.append(
                    {
                        "input": {
                            "material": mid.value, "tool": tid.value,
                            "operation": oid.value, "raReq": ra,
                            "volume": 100000.0,
                        },
                        "output": {
                            "vc": round6(p.vc),
                            "fz": round6(p.fz),
                            "ap": round6(p.ap),
                            "ae": round6(p.ae),
                            "n": round6(p.n),
                            "pc": round6(p.pc),
                            "raPred": round6(p.ra_pred),
                            "toolLife": round6(p.tool_life),
                            "strategy": p.strategy.value,
                            "chatterRisk": p.chatter_risk,
                            "confidence": p.confidence.value,
                            "altCount": len(rec.alternatives),
                        },
                    }
                )
    return vectors


def build_stable_bands() -> dict:
    """稳定带中心值（按齿数）。"""
    return {
        str(z): {str(k): round6(chatter_stable_rpm(z, k)) for k in (1, 2, 3)}
        for z in (2, 4, 5)
    }


def build_knowledge() -> dict:
    """知识库条目与检索基准。"""
    kb = default_knowledge_base()
    items = [
        {
            "kbId": it.kb_id,
            "title": it.title,
            "sourceType": it.source_type,
            "sourceRef": it.source_ref,
            "scope": it.scope,
            "metrics": it.metrics,
            "confidence": it.confidence.value,
            "body": it.body,
        }
        for it in kb.index.items
    ]
    queries = ["45钢 侧铣 切削速度", "钛合金 TC4 精加工", "304 不锈钢 内冷",
               "铝合金 6061 高速铣削", "主轴功率 限制", "火星探测器轨道参数"]
    return {
        "items": items,
        "queries": [
            {"q": q, "hits": [h.item.kb_id for h in kb.search(q)]} for q in queries
        ],
        "tokens": {"45钢 vc=180": tokenize("45钢 vc=180")},
    }


def main() -> int:
    """主入口。"""
    data = {
        "meta": {
            "generatedFrom": "zrcut (Python)",
            "tolerance": 0.0001,
            "note": "JS 引擎须与 Python 端数值一致，相对误差 ≤ 1e-4",
        },
        "domain": build_domain(),
        "calc": build_calc_vectors(),
        "validation": build_validation_vectors(),
        "converge": build_converge_vectors(),
        "chatter": build_chatter_vectors(),
        "recommend": build_recommend_vectors(),
        "stableBands": build_stable_bands(),
        "knowledge": build_knowledge(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[写出] {OUT}")
    print(f"[大小] {OUT.stat().st_size / 1024:.1f} KB")
    for key in ("calc", "validation", "converge", "chatter", "recommend"):
        print(f"  {key}: {len(data[key])} 条向量")
    print(f"  knowledge: {len(data['knowledge']['items'])} 条目 / {len(data['knowledge']['queries'])} 查询")
    return 0


if __name__ == "__main__":
    sys.exit(main())
