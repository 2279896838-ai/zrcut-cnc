"""回填闭环与偏差判定。

对应《提示词工程规范》4.6 节。操作工试切后回填实测数据，系统比对预测与
实测偏差，按三档阈值决定：仅入库 / 提示调整 / 冻结组合并转工艺工程师。

关键约束：禁止在线学习。回填数据只进入样本池，模型更新必须走离线增量
训练 + 灰度发布流程（见 retrain 函数的文档）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Sequence

from .domain import (
    CLOSURE_LIFE_TOLERANCE,
    CLOSURE_RA_TOLERANCE,
    CLOSURE_TIME_TOLERANCE,
    Confidence,
    Material,
    Tool,
)
from .errors import ErrorCode, ZrCutError


class DeviationLevel(str, Enum):
    """偏差三档。"""

    NORMAL = "NORMAL"
    MEDIUM = "MEDIUM"
    SEVERE = "SEVERE"


class Disposition(str, Enum):
    """处置方式。"""

    RECORD_ONLY = "RECORD_ONLY"
    ADJUST_HINT = "ADJUST_HINT"
    FREEZE_AND_ESCALATE = "FREEZE_AND_ESCALATE"


RECORD_RE = re.compile(r"^TR-\d{8}-\d{4}$")


@dataclass(slots=True)
class ClosureRecord:
    """试切回填记录。

    Attributes:
        trace_id: 试切记录号 TR-YYYYMMDD-NNNN。
        card_id: 关联工艺卡号。
        material_code: 材料代号。
        tool_code: 刀具代号。
        predicted_ra: 预测粗糙度，μm。
        actual_ra: 实测粗糙度，μm。
        predicted_time: 预测工时，min。
        actual_time: 实测工时，min。
        predicted_life: 预测寿命，min。
        actual_life: 实测寿命，min。
        tool_wear_vb: 刀具后刀面磨损 VB，mm。
        chatter_observed: 是否观察到颤振。
        anomaly: 异常描述。
        operator_note: 操作工备注。
        recorded_at: 记录时间。
        recorded_by: 记录人。
    """

    trace_id: str
    card_id: str
    material_code: str
    tool_code: str
    predicted_ra: float | None = None
    actual_ra: float | None = None
    predicted_time: float | None = None
    actual_time: float | None = None
    predicted_life: float | None = None
    actual_life: float | None = None
    tool_wear_vb: float | None = None
    chatter_observed: bool = False
    anomaly: str = ""
    operator_note: str = ""
    recorded_at: str = field(
        default_factory=lambda: datetime.now().replace(microsecond=0).isoformat()
    )
    recorded_by: str = ""

    def __post_init__(self) -> None:
        if not RECORD_RE.match(self.trace_id):
            raise ZrCutError(
                ErrorCode.CLOSURE_INVALID,
                f"试切记录号不合规：{self.trace_id}；须形如 TR-20261109-0003",
                {"traceId": self.trace_id},
            )
        for name, value in (
            ("实测粗糙度", self.actual_ra),
            ("实测工时", self.actual_time),
            ("实测寿命", self.actual_life),
        ):
            if value is not None and value <= 0:
                raise ZrCutError(
                    ErrorCode.CLOSURE_INVALID,
                    f"{name}必须为正，当前 {value}",
                    {"field": name, "value": value},
                )

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return asdict(self)


def relative_deviation(predicted: float | None, actual: float | None) -> float | None:
    """计算相对偏差 (predicted - actual) / actual。

    Args:
        predicted: 预测值。
        actual: 实测值。

    Returns:
        相对偏差；任一为空时返回 None。

    Raises:
        ZrCutError: 实测值非正。
    """
    if predicted is None or actual is None:
        return None
    if actual <= 0:
        raise ZrCutError(
            ErrorCode.CLOSURE_INVALID,
            f"实测值必须为正，当前 {actual}",
            {"actual": actual},
        )
    return (predicted - actual) / actual


@dataclass(frozen=True, slots=True)
class ClosureAssessment:
    """回填评估结果。

    Attributes:
        level: 偏差档位。
        disposition: 处置方式。
        ra_deviation: 粗糙度相对偏差。
        time_deviation: 工时相对偏差。
        life_deviation: 寿命相对偏差。
        triggers: 命中的判定条件列表。
        reasons: 人类可读的判定说明。
        hint: 给操作工/工艺员的建议。
        confidence_after: 该组合置信度调整后的建议值。
    """

    level: DeviationLevel
    disposition: Disposition
    ra_deviation: float | None
    time_deviation: float | None
    life_deviation: float | None
    triggers: list[str]
    reasons: list[str]
    hint: str
    confidence_after: Confidence

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "level": self.level.value,
            "disposition": self.disposition.value,
            "raDeviation": (
                round(self.ra_deviation, 4) if self.ra_deviation is not None else None
            ),
            "timeDeviation": (
                round(self.time_deviation, 4) if self.time_deviation is not None else None
            ),
            "lifeDeviation": (
                round(self.life_deviation, 4) if self.life_deviation is not None else None
            ),
            "triggers": self.triggers,
            "reasons": self.reasons,
            "hint": self.hint,
            "confidenceAfter": self.confidence_after.value,
        }


def assess_closure(record: ClosureRecord) -> ClosureAssessment:
    """按三档阈值判定回填偏差。

    阈值（规范 4.6 节）：
        Ra   偏差 > 25% 触发
        工时 偏差 > 30% 触发
        寿命 偏差 > 40% 触发
    三档：
        NORMAL  全部未触发 → 仅入库
        MEDIUM  触发 1 项    → 提示调整
        SEVERE  触发 ≥2 项或单项超阈值两倍 → 冻结并转工艺工程师

    Args:
        record: 回填记录。

    Returns:
        ClosureAssessment 实例。
    """
    ra_d = relative_deviation(record.predicted_ra, record.actual_ra)
    tm_d = relative_deviation(record.predicted_time, record.actual_time)
    life_d = relative_deviation(record.predicted_life, record.actual_life)

    triggers: list[str] = []
    reasons: list[str] = []
    severe_extra = False

    if ra_d is not None:
        a = abs(ra_d)
        if a > CLOSURE_RA_TOLERANCE:
            triggers.append("RA")
            reasons.append(
                f"粗糙度偏差 {ra_d * 100:+.1f}%，超阈值 {CLOSURE_RA_TOLERANCE * 100:.0f}%"
            )
            if a > CLOSURE_RA_TOLERANCE * 2:
                severe_extra = True

    if tm_d is not None:
        a = abs(tm_d)
        if a > CLOSURE_TIME_TOLERANCE:
            triggers.append("TIME")
            reasons.append(
                f"工时偏差 {tm_d * 100:+.1f}%，超阈值 {CLOSURE_TIME_TOLERANCE * 100:.0f}%"
            )
            if a > CLOSURE_TIME_TOLERANCE * 2:
                severe_extra = True

    if life_d is not None:
        a = abs(life_d)
        if a > CLOSURE_LIFE_TOLERANCE:
            triggers.append("LIFE")
            reasons.append(
                f"刀具寿命偏差 {life_d * 100:+.1f}%，超阈值 {CLOSURE_LIFE_TOLERANCE * 100:.0f}%"
            )
            if a > CLOSURE_LIFE_TOLERANCE * 2:
                severe_extra = True

    if record.chatter_observed:
        # 现场观察到颤振说明颤振判据与实际不符，必须作为独立触发项，
        # 否则会被「无偏差」分支误判为 NORMAL，导致判据永不修正。
        triggers.append("CHATTER")
        reasons.append("现场观察到颤振，颤振判据需重新标定")

    if not triggers:
        level = DeviationLevel.NORMAL
        disposition = Disposition.RECORD_ONLY
        hint = "偏差在阈值内，数据入库，无需调整参数"
        conf = Confidence.HIGH
    elif (
        len(triggers) >= 2
        or severe_extra
        or record.chatter_observed
    ):
        level = DeviationLevel.SEVERE
        disposition = Disposition.FREEZE_AND_ESCALATE
        hint = (
            "偏差严重或现场出现颤振，冻结该材料+刀具组合的自动推荐，"
            "转工艺工程师复核；复核通过前不得再用自动推荐结果开切"
        )
        conf = Confidence.LOW
    else:
        level = DeviationLevel.MEDIUM
        disposition = Disposition.ADJUST_HINT
        hint = _medium_hint(triggers, ra_d, tm_d, life_d)
        conf = Confidence.MEDIUM

    return ClosureAssessment(
        level=level,
        disposition=disposition,
        ra_deviation=ra_d,
        time_deviation=tm_d,
        life_deviation=life_d,
        triggers=triggers,
        reasons=reasons or ["全部指标偏差在阈值内"],
        hint=hint,
        confidence_after=conf,
    )


def _medium_hint(
    triggers: Sequence[str],
    ra_d: float | None,
    tm_d: float | None,
    life_d: float | None,
) -> str:
    """为中档偏差生成具体调整建议。

    偏差定义：(predicted - actual) / actual。
    Ra 场景下 predicted 越小越好，因此偏差为负表示实测比预测更差（劣化）；
    工时与寿命场景下 predicted 越大越好，偏差为正表示实测低于预期（劣化）。
    """
    hints: list[str] = []
    if "RA" in triggers and ra_d is not None:
        if ra_d < 0:
            hints.append("实测粗糙度劣于预测，fz 下调 10%，或改用更小 rε 的刀具")
        else:
            hints.append("实测粗糙度优于预测，可保持参数并在下批提升进给 5%")
    if "TIME" in triggers and tm_d is not None:
        if tm_d < 0:
            hints.append("实测工时超出预测，检查空行程与换刀次数，必要时提高 ap 减少走刀层数")
        else:
            hints.append("实测工时短于预测，可复核程序中的进给倍率设置")
    if "LIFE" in triggers and life_d is not None:
        if life_d > 0:
            hints.append("实测寿命短于预测，vc 下调 8% 并缩短换刀周期")
        else:
            hints.append("实测寿命长于预测，可在保持质量前提下提高 vc 回收效率")
    return "；".join(hints) if hints else "建议小幅下调参数后复切验证"


@dataclass(slots=True)
class ClosureStore:
    """内存回填记录仓库，提供聚合查询。

    Attributes:
        records: 记录列表。
        frozen: 被冻结的材料+刀具组合集合。
    """

    records: list[ClosureRecord] = field(default_factory=list)
    frozen: set[tuple[str, str]] = field(default_factory=set)

    def add(self, record: ClosureRecord) -> ClosureAssessment:
        """写入回填记录并返回评估。

        Args:
            record: 回填记录。

        Returns:
            评估结果。

        Raises:
            ZrCutError: 记录号重复。
        """
        if any(r.trace_id == record.trace_id for r in self.records):
            raise ZrCutError(
                ErrorCode.CLOSURE_INVALID,
                f"试切记录号已存在：{record.trace_id}",
                {"traceId": record.trace_id},
            )
        self.records.append(record)
        assessment = assess_closure(record)
        if assessment.disposition is Disposition.FREEZE_AND_ESCALATE:
            self.frozen.add((record.material_code, record.tool_code))
        return assessment

    def is_frozen(self, material_code: str, tool_code: str) -> bool:
        """判断组合是否被冻结。"""
        return (material_code, tool_code) in self.frozen

    def stats(self, material_code: str | None = None, tool_code: str | None = None) -> dict[str, Any]:
        """统计指定组合的回填聚合指标。

        Args:
            material_code: 材料代号过滤。
            tool_code: 刀具代号过滤。

        Returns:
            含样本数、平均偏差、档位分布的字典。
        """
        subset = [
            r
            for r in self.records
            if (material_code is None or r.material_code == material_code)
            and (tool_code is None or r.tool_code == tool_code)
        ]
        if not subset:
            return {"nSamples": 0}

        ra_devs = [
            d
            for d in (relative_deviation(r.predicted_ra, r.actual_ra) for r in subset)
            if d is not None
        ]
        tm_devs = [
            d
            for d in (relative_deviation(r.predicted_time, r.actual_time) for r in subset)
            if d is not None
        ]
        life_devs = [
            d
            for d in (relative_deviation(r.predicted_life, r.actual_life) for r in subset)
            if d is not None
        ]

        def mean(xs: list[float]) -> float | None:
            return sum(xs) / len(xs) if xs else None

        levels: dict[str, int] = {}
        for r in subset:
            lv = assess_closure(r).level.value
            levels[lv] = levels.get(lv, 0) + 1

        return {
            "nSamples": len(subset),
            "raDeviationMean": _round(mean(ra_devs)),
            "raDeviationMae": _round(mean([abs(x) for x in ra_devs])),
            "timeDeviationMean": _round(mean(tm_devs)),
            "lifeDeviationMean": _round(mean(life_devs)),
            "levelDistribution": levels,
            "chatterCount": sum(1 for r in subset if r.chatter_observed),
        }

    def to_jsonl(self) -> str:
        """导出为 JSON Lines。"""
        return "\n".join(
            json.dumps(r.to_dict(), ensure_ascii=False) for r in self.records
        )


def _round(x: float | None) -> float | None:
    """四舍五入到 4 位，None 透传。"""
    return round(x, 4) if x is not None else None


def retrain(
    store: ClosureStore,
    *,
    material_code: str,
    tool_code: str,
    min_samples: int = 3,
) -> dict[str, Any]:
    """离线增量训练的准入判定（不执行实际训练）。

    本函数只回答「这批样本够不够训练」，把训练动作留给离线任务。
    这是刻意设计：在线请求路径绝不允许触发模型更新。

    Args:
        store: 回填仓库。
        material_code: 材料代号。
        tool_code: 刀具代号。
        min_samples: 最小样本数。

    Returns:
        含 eligible、nSamples、reasons、nextAction 的字典。

    Note:
        训练流程（离线执行）：
            1. 抽取该材料+刀具组合的回填记录
            2. 特征工程：硬度、Kc、D、z、rε、vc、fz、ap
            3. 增量拟合比值模型
            4. 与线上模型做影子对比，一致率 ≥95% 方可灰度
            5. 灰度 10% 观察 7 日无越限漏检后全量
    """
    if store.is_frozen(material_code, tool_code):
        return {
            "eligible": False,
            "nSamples": 0,
            "reasons": [f"组合 {material_code}+{tool_code} 处于冻结状态，须工艺工程师解冻"],
            "nextAction": "FREEZE_BLOCKED",
        }

    subset = [
        r
        for r in store.records
        if r.material_code == material_code and r.tool_code == tool_code
    ]
    n = len(subset)
    reasons: list[str] = []
    if n < min_samples:
        reasons.append(f"样本数 {n} 少于最小要求 {min_samples}，暂不训练")
    severe = sum(
        1 for r in subset if assess_closure(r).level is DeviationLevel.SEVERE
    )
    if severe:
        reasons.append(f"存在 {severe} 条严重偏差样本，须先人工复核标注")

    eligible = n >= min_samples and severe == 0
    return {
        "eligible": eligible,
        "nSamples": n,
        "reasons": reasons or ["样本充足且无严重偏差，可进入离线增量训练"],
        "nextAction": "OFFLINE_TRAIN" if eligible else "COLLECT_MORE",
    }
