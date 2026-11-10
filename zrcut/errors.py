"""统一错误码与异常体系。

工业系统要求错误可归因、可检索、可对前端稳定暴露。所有对外异常都必须
携带稳定的错误码，禁止把 Python 内部异常类名直接暴露给调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """稳定错误码枚举。"""

    # 输入类 1xxx
    INVALID_INPUT = "E1001"
    MISSING_SLOT = "E1002"
    UNKNOWN_MATERIAL = "E1003"
    UNKNOWN_TOOL = "E1004"
    UNKNOWN_OPERATION = "E1005"
    TOOL_OPERATION_MISMATCH = "E1006"
    INVALID_PARAMETER_RANGE = "E1007"

    # 计算类 2xxx
    CALCULATION_FAILED = "E2001"
    NO_FEASIBLE_PLAN = "E2002"
    CONVERGENCE_FAILED = "E2003"

    # 约束类 3xxx
    CONSTRAINT_VIOLATED = "E3001"
    MACHINE_LIMIT_EXCEEDED = "E3002"
    CHATTER_HIGH = "E3003"
    ROUGHNESS_UNACHIEVABLE = "E3004"

    # 知识类 4xxx
    KB_EMPTY = "E4001"
    KB_NO_MATCH = "E4002"
    KB_CONFLICT = "E4003"
    KB_ENTRY_INVALID = "E4004"

    # 工艺卡类 5xxx
    CARD_NOT_FOUND = "E5001"
    CARD_LOCKED = "E5002"
    CARD_INVALID = "E5003"

    # 回填类 6xxx
    CLOSURE_NOT_FOUND = "E6001"
    CLOSURE_INVALID = "E6002"

    # 安全类 7xxx
    UNSAFE_SETUP = "E7001"
    ROLE_RESTRICTED = "E7002"
    COOLANT_REQUIRED = "E7003"
    SKIP_TRIAL_CUT = "E7004"

    # 系统类 9xxx
    LLM_UNAVAILABLE = "E9001"
    SCHEMA_INVALID = "E9002"
    INTERNAL_ERROR = "E9999"


@dataclass(slots=True)
class ZrCutError(Exception):
    """系统内所有业务异常的基类。

    Attributes:
        code: 稳定错误码。
        message: 面向用户的中文说明。
        detail: 面向排障的结构化细节。
        http_status: 建议的 HTTP 状态码。
    """

    code: ErrorCode
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    http_status: int = 400

    def __str__(self) -> str:
        """字符串表示。"""
        return f"[{self.code.value}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """转字典，供 API 返回。"""
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "detail": self.detail,
            }
        }


def invalid_input(message: str, **detail: Any) -> ZrCutError:
    """构造输入错误。"""
    return ZrCutError(ErrorCode.INVALID_INPUT, message, detail)


def unknown_material(code: str) -> ZrCutError:
    """构造未知材料错误。"""
    return ZrCutError(
        ErrorCode.UNKNOWN_MATERIAL,
        f"未登记的材料代号：{code}；可用代号 M1-M5",
        {"material": code},
    )


def unknown_tool(code: str) -> ZrCutError:
    """构造未知刀具错误。"""
    return ZrCutError(
        ErrorCode.UNKNOWN_TOOL,
        f"未登记的刀具代号：{code}；可用代号 T1-T6",
        {"tool": code},
    )


def unknown_operation(code: str) -> ZrCutError:
    """构造未知工序错误。"""
    return ZrCutError(
        ErrorCode.UNKNOWN_OPERATION,
        f"未登记的工序代号：{code}；可用代号 OP1-OP4",
        {"operation": code},
    )


def no_feasible_plan(message: str, **detail: Any) -> ZrCutError:
    """构造无可行方案错误。"""
    return ZrCutError(ErrorCode.NO_FEASIBLE_PLAN, message, detail)
