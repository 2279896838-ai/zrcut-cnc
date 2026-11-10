"""工艺卡生成、签发锁定与扫码内容规范。

对应《提示词工程规范》4.5 节。工艺卡一旦签发即锁定，任何修改必须走
变更单流程，不得就地覆盖 —— 这是可审计性的基础。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Any

from .domain import Confidence, Material, Tool
from .errors import ErrorCode, ZrCutError
from .recommender import Plan, Recommendation


class CardStatus(str, Enum):
    """工艺卡状态。"""

    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    LOCKED = "LOCKED"
    SUPERSEDED = "SUPERSEDED"


class ExportFormat(str, Enum):
    """导出形态。"""

    MARKDOWN = "MARKDOWN"
    JSON = "JSON"
    QR = "QR"


CARD_ID_RE = re.compile(r"^PC-\d{8}-\d{4}$")


@dataclass(slots=True)
class ProcessCard:
    """工艺卡。

    14 个必填字段 + 4 个附注字段，对应规范 4.5 节。

    Attributes:
        card_id: 卡号 PC-YYYYMMDD-NNNN。
        part_name: 零件名。
        material_code: 材料代号。
        blank_size: 毛坯尺寸，mm。
        op_no: 工序号。
        op_name: 工序名。
        machine_code: 机床编码。
        tool_code: 刀具代号。
        spindle_speed: 主轴转速 n，r/min。
        feed_rate: 进给速度 vf，mm/min。
        feed_per_tooth: 每齿进给 fz，mm/z。
        axial_depth: 轴向切深 ap，mm。
        radial_width: 径向切宽 ae，mm。
        coolant: 冷却方式。
        est_machining_time: 预计工时，min。
        est_roughness: 预计粗糙度 Ra，μm。
        chatter_risk: 颤振等级。
        first_article_req: 首件检验要求。
        status: 卡状态。
        issued_by: 签发人。
        issued_at: 签发时间。
        revision: 修订号。
        notes: 收敛动作等备注。
        supersedes: 被替代的卡号。
    """

    card_id: str
    part_name: str
    material_code: str
    blank_size: str
    op_no: int
    op_name: str
    machine_code: str
    tool_code: str
    spindle_speed: float
    feed_rate: float
    feed_per_tooth: float
    axial_depth: float
    radial_width: float
    coolant: str
    est_machining_time: float | None = None
    est_roughness: float | None = None
    chatter_risk: str = "LOW"
    first_article_req: str = "首件三点实测 Ra 并读功率表"
    status: CardStatus = CardStatus.DRAFT
    issued_by: str | None = None
    issued_at: str | None = None
    revision: int = 1
    notes: list[str] = field(default_factory=list)
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not CARD_ID_RE.match(self.card_id):
            raise ZrCutError(
                ErrorCode.CARD_INVALID,
                f"工艺卡号不合规：{self.card_id}；须形如 PC-20261109-0007",
                {"cardId": self.card_id},
            )
        for name, value in (
            ("主轴转速", self.spindle_speed),
            ("进给速度", self.feed_rate),
            ("每齿进给", self.feed_per_tooth),
            ("轴向切深", self.axial_depth),
            ("径向切宽", self.radial_width),
        ):
            if value <= 0:
                raise ZrCutError(
                    ErrorCode.CARD_INVALID,
                    f"{name}必须为正，当前 {value}",
                    {"field": name, "value": value},
                )

    @property
    def content_hash(self) -> str:
        """工艺卡内容的稳定哈希，用于防篡改校验。"""
        payload = {
            "cardId": self.card_id,
            "material": self.material_code,
            "tool": self.tool_code,
            "n": round(self.spindle_speed, 1),
            "vf": round(self.feed_rate, 1),
            "fz": round(self.feed_per_tooth, 4),
            "ap": round(self.axial_depth, 2),
            "ae": round(self.radial_width, 2),
            "revision": self.revision,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def issue(self, issued_by: str) -> None:
        """签发工艺卡并锁定。

        Args:
            issued_by: 签发人标识。

        Raises:
            ZrCutError: 已签发状态下重复签发。
        """
        if self.status in (CardStatus.ISSUED, CardStatus.LOCKED):
            raise ZrCutError(
                ErrorCode.CARD_LOCKED,
                f"工艺卡 {self.card_id} 已签发，不得重复签发；修改请走变更单",
                {"cardId": self.card_id, "status": self.status.value},
            )
        self.status = CardStatus.LOCKED
        self.issued_by = issued_by
        self.issued_at = datetime.now().replace(microsecond=0).isoformat()

    def revise(self, changes: dict[str, Any], *, reason: str) -> "ProcessCard":
        """基于本卡生成修订版，返回新卡，原卡标记为被替代。

        Args:
            changes: 变更字段字典。
            reason: 变更原因，必填。

        Returns:
            新的工艺卡实例。

        Raises:
            ZrCutError: 未携带变更原因，或试图修改卡号。
        """
        if not reason:
            raise ZrCutError(
                ErrorCode.CARD_INVALID, "变更必须填写原因", {"cardId": self.card_id}
            )
        if "card_id" in changes:
            raise ZrCutError(
                ErrorCode.CARD_INVALID, "不得在修订中修改卡号", {"cardId": self.card_id}
            )
        payload = asdict(self)
        payload.pop("notes", None)
        payload["notes"] = list(self.notes) + [f"修订原因：{reason}"]
        payload.update(changes)
        payload["revision"] = self.revision + 1
        payload["status"] = CardStatus.DRAFT
        payload["issued_by"] = None
        payload["issued_at"] = None
        payload["supersedes"] = self.card_id
        new_card = ProcessCard(**payload)
        self.status = CardStatus.SUPERSEDED
        return new_card

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "cardId": self.card_id,
            "partName": self.part_name,
            "materialCode": self.material_code,
            "blankSize": self.blank_size,
            "opNo": self.op_no,
            "opName": self.op_name,
            "machineCode": self.machine_code,
            "toolCode": self.tool_code,
            "spindleSpeed": round(self.spindle_speed, 1),
            "feedRate": round(self.feed_rate, 1),
            "feedPerTooth": round(self.feed_per_tooth, 4),
            "axialDepth": round(self.axial_depth, 2),
            "radialWidth": round(self.radial_width, 2),
            "coolant": self.coolant,
            "estMachiningTime": (
                round(self.est_machining_time, 3)
                if self.est_machining_time is not None
                else None
            ),
            "estRoughness": (
                round(self.est_roughness, 3) if self.est_roughness is not None else None
            ),
            "chatterRisk": self.chatter_risk,
            "firstArticleReq": self.first_article_req,
            "status": self.status.value,
            "issuedBy": self.issued_by,
            "issuedAt": self.issued_at,
            "revision": self.revision,
            "notes": list(self.notes),
            "supersedes": self.supersedes,
            "contentHash": self.content_hash,
        }

    def to_markdown(self) -> str:
        """导出为 Markdown 表格。"""
        rows = [
            ("卡号", self.card_id),
            ("零件名", self.part_name),
            ("材料代号", self.material_code),
            ("毛坯尺寸", self.blank_size),
            ("工序号", str(self.op_no)),
            ("工序名", self.op_name),
            ("机床编码", self.machine_code),
            ("刀具代号", self.tool_code),
            ("主轴转速 n", f"{self.spindle_speed:.1f} r/min"),
            ("进给速度 vf", f"{self.feed_rate:.1f} mm/min"),
            ("每齿进给 fz", f"{self.feed_per_tooth:.4f} mm/z"),
            ("轴向切深 ap", f"{self.axial_depth:.2f} mm"),
            ("径向切宽 ae", f"{self.radial_width:.2f} mm"),
            ("冷却方式", self.coolant),
            ("预计工时", f"{self.est_machining_time:.3f} min" if self.est_machining_time else "—"),
            ("预计粗糙度 Ra", f"{self.est_roughness:.3f} μm" if self.est_roughness else "—"),
            ("颤振等级", self.chatter_risk),
            ("首件检验要求", self.first_article_req),
            ("状态", self.status.value),
            ("修订号", str(self.revision)),
        ]
        lines = [
            f"# 工艺卡 {self.card_id}",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
        ]
        lines.extend(f"| {k} | {v} |" for k, v in rows)
        if self.notes:
            lines.extend(["", "## 收敛与风险备注", ""])
            lines.extend(f"- {n}" for n in self.notes)
        lines.extend(
            [
                "",
                f"内容哈希：`{self.content_hash}`（签发后锁定，修改须走变更单）",
            ]
        )
        return "\n".join(lines)

    def qr_payload(self) -> str:
        """扫码内容规范：紧凑的键值串，便于车间扫码枪与移动端解析。

        Returns:
            以 | 分隔的紧凑字符串。
        """
        return "|".join(
            [
                "ZRCUT",
                self.card_id,
                self.material_code,
                self.tool_code,
                f"n={self.spindle_speed:.0f}",
                f"vf={self.feed_rate:.0f}",
                f"ap={self.axial_depth:.1f}",
                f"ae={self.radial_width:.1f}",
                f"rev={self.revision}",
                self.content_hash,
            ]
        )


_counter: dict[str, int] = {}


def next_card_id(today: date | None = None) -> str:
    """生成当日递增的工艺卡号。

    Args:
        today: 日期，默认今天。

    Returns:
        形如 PC-20261109-0007 的卡号。
    """
    d = today or date.today()
    key = d.strftime("%Y%m%d")
    _counter[key] = _counter.get(key, 0) + 1
    return f"PC-{key}-{_counter[key]:04d}"


def build_card(
    recommendation: Recommendation,
    *,
    part_name: str,
    blank_size: str,
    op_no: int,
    card_id: str | None = None,
    material: Material | None = None,
    tool: Tool | None = None,
) -> ProcessCard:
    """由推荐结果生成工艺卡。

    Args:
        recommendation: 推荐结果。
        part_name: 零件名。
        blank_size: 毛坯尺寸描述。
        op_no: 工序号。
        card_id: 指定卡号；None 则自动生成。
        material: 材料对象，None 时从推荐结果取。
        tool: 刀具对象，None 时从推荐结果取。

    Returns:
        ProcessCard 实例（草稿状态）。

    Raises:
        ZrCutError: 推荐结果缺少必要信息。
    """
    plan: Plan = recommendation.primary
    mat = material or recommendation.material
    tl = tool or recommendation.tool
    if mat is None or tl is None:
        raise ZrCutError(
            ErrorCode.CARD_INVALID,
            "推荐结果缺少材料或刀具信息，无法生成工艺卡",
            {},
        )

    from .domain import MACHINE

    notes = list(recommendation.warnings)
    if recommendation.validation and recommendation.validation.iterations > 0:
        history = recommendation.validation.history
        notes.append(
            f"参数经 {recommendation.validation.iterations} 轮收敛："
            + " → ".join(
                f"ap={h['ap']:.2f}/ae={h['ae']:.2f}" for h in history if h["iteration"] > 0
            )
        )

    return ProcessCard(
        card_id=card_id or next_card_id(),
        part_name=part_name,
        material_code=mat.code.value,
        blank_size=blank_size,
        op_no=op_no,
        op_name="侧铣" if recommendation.request.operation.value.startswith("OP2") else recommendation.request.operation.value,
        machine_code=MACHINE.code,
        tool_code=tl.code.value,
        spindle_speed=plan.n,
        feed_rate=plan.vf,
        feed_per_tooth=plan.fz,
        axial_depth=plan.ap,
        radial_width=plan.ae,
        coolant=MACHINE.coolant_type,
        est_machining_time=plan.machining_time,
        est_roughness=plan.ra_pred,
        chatter_risk=plan.chatter_risk,
        first_article_req=(
            "首件三点实测 Ra、读机床功率表核对、监听异响"
            if plan.chatter_risk != "LOW"
            else "首件三点实测 Ra"
        ),
        notes=notes,
    )


def verify_card(card: ProcessCard, expected_hash: str) -> bool:
    """校验工艺卡内容与给定哈希是否一致。

    Args:
        card: 工艺卡。
        expected_hash: 期望的内容哈希。

    Returns:
        一致返回 True。
    """
    return card.content_hash == expected_hash
