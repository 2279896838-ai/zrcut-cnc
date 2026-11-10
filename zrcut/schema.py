"""轻量 JSON Schema 校验器（零依赖）。

只实现本项目实际用到的子集：type、properties、required、
additionalProperties、items、enum、const、minimum、maximum、
minItems、maxItems、pattern、oneOf。刻意不支持 $ref 与远程引用，
避免引入隐式网络依赖。
"""

from __future__ import annotations

import re
from typing import Any

from .errors import ErrorCode, ZrCutError

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ZrCutError):
    """Schema 校验失败。"""

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(ErrorCode.SCHEMA_INVALID, message, detail, 422)


def _is_number(x: Any) -> bool:
    """是否为数字但不含布尔。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_type(value: Any, expected: str | list[str], path: str) -> None:
    """校验基础类型，支持联合类型（字符串列表）。"""
    if isinstance(expected, list):
        errors: list[str] = []
        for alt in expected:
            try:
                _check_type(value, alt, path)
                return
            except SchemaError as exc:
                errors.append(str(exc))
        raise SchemaError(
            f"{path} 不匹配任何允许的类型 {expected}，实际 {type(value).__name__}",
            branches=errors,
        )

    if expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SchemaError(f"{path} 应为 integer，实际 {type(value).__name__}")
        return
    if expected == "number":
        if not _is_number(value):
            raise SchemaError(f"{path} 应为 number，实际 {type(value).__name__}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise SchemaError(f"{path} 应为 array，实际 {type(value).__name__}")
        return
    if expected in _TYPES:
        if not isinstance(value, _TYPES[expected]):
            raise SchemaError(f"{path} 应为 {expected}，实际 {type(value).__name__}")
        return
    raise SchemaError(f"{path} 使用了不支持的 type：{expected}")


def validate_schema(instance: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """按 JSON Schema 子集校验实例。

    Args:
        instance: 待校验数据。
        schema: Schema 字典。
        path: 当前路径，用于错误定位。

    Raises:
        SchemaError: 校验失败。
    """
    if "oneOf" in schema:
        errors: list[str] = []
        for i, sub in enumerate(schema["oneOf"]):
            try:
                validate_schema(instance, sub, path=f"{path}.oneOf[{i}]")
                return
            except SchemaError as exc:
                errors.append(str(exc))
        raise SchemaError(f"{path} 未匹配任何 oneOf 分支", branches=errors)

    if "const" in schema and instance != schema["const"]:
        raise SchemaError(f"{path} 应恒等于 {schema['const']!r}，实际 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path} 取值 {instance!r} 不在枚举 {schema['enum']} 内")

    if "type" in schema:
        _check_type(instance, schema["type"], path)

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaError(f"{path} 小于下界 {schema['minimum']}，实际 {instance}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaError(f"{path} 大于上界 {schema['maximum']}，实际 {instance}")

    if isinstance(instance, str) and "pattern" in schema:
        if not re.search(schema["pattern"], instance):
            raise SchemaError(f"{path} 不匹配模式 {schema['pattern']}：{instance!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path} 缺少必填字段 {key}")
        props: dict[str, Any] = schema.get("properties", {})
        for key, val in instance.items():
            if key in props:
                validate_schema(val, props[key], path=f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path} 出现未定义字段 {key}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaError(
                f"{path} 元素数 {len(instance)} 少于 {schema['minItems']}"
            )
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaError(
                f"{path} 元素数 {len(instance)} 多于 {schema['maxItems']}"
            )
        if "items" in schema:
            for i, item in enumerate(instance):
                validate_schema(item, schema["items"], path=f"{path}[{i}]")


# ---------------------------------------------------------------------------
# 六类输出 Schema，与提示词规范第 7 章一致
# ---------------------------------------------------------------------------

CONFIDENCE_ENUM = ["HIGH", "MEDIUM", "LOW"]
CHATTER_ENUM = ["LOW", "MEDIUM", "HIGH"]
REFUSAL_ENUM = [
    "MACHINE_LIMIT",
    "NO_EVIDENCE",
    "SKIP_TRIAL_CUT",
    "UNSAFE_SETUP",
    "ROLE_RESTRICTED",
    "CARD_LOCKED",
]

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
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
        "raTh",
        "raPred",
        "toolLife",
        "chatterRisk",
        "confidence",
    ],
    "additionalProperties": True,
    "properties": {
        "label": {"type": "string", "pattern": "^P[0-9]$"},
        "strategy": {
            "type": "string",
            "enum": ["EFFICIENCY", "QUALITY", "LIFE", "BALANCED"],
        },
        "vc": {"type": "number", "minimum": 0, "maximum": 2000},
        "fz": {"type": "number", "minimum": 0, "maximum": 5},
        "ap": {"type": "number", "minimum": 0, "maximum": 200},
        "ae": {"type": "number", "minimum": 0, "maximum": 200},
        "n": {"type": "number", "minimum": 0, "maximum": 8000},
        "vf": {"type": "number", "minimum": 0, "maximum": 10000},
        "mrr": {"type": "number", "minimum": 0},
        "pc": {"type": "number", "minimum": 0, "maximum": 11},
        "raTh": {"type": "number", "minimum": 0},
        "raPred": {"type": "number", "minimum": 0},
        "toolLife": {"type": "number", "minimum": 0},
        "machiningTime": {"type": ["number", "null"], "minimum": 0},
        "chatterRisk": {"type": "string", "enum": CHATTER_ENUM},
        "confidence": {"type": "string", "enum": CONFIDENCE_ENUM},
        "note": {"type": "string"},
    },
}

PARAMETER_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["request", "primary", "alternatives", "warnings"],
    "additionalProperties": True,
    "properties": {
        "request": {
            "type": "object",
            "required": ["material", "tool", "operation", "raReq"],
            "properties": {
                "material": {"type": "string", "pattern": "^M[1-5]$"},
                "tool": {"type": "string", "pattern": "^T[1-6]$"},
                "operation": {"type": "string", "pattern": "^OP[1-4]$"},
                "raReq": {"type": "number", "minimum": 0},
                "strategy": {"type": ["string", "null"]},
                "volume": {"type": ["number", "null"], "minimum": 0},
            },
        },
        "primary": PLAN_SCHEMA,
        "alternatives": {"type": "array", "items": PLAN_SCHEMA, "maxItems": 4},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "rationality": {"type": "string"},
    },
}

CONSTRAINT_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["checkId", "constraint", "passed", "actual", "limit"],
    "additionalProperties": True,
    "properties": {
        "checkId": {"type": "string", "pattern": "^C-(0[1-9]|10)$"},
        "constraint": {"type": "string"},
        "passed": {"type": "boolean"},
        "actual": {"type": ["number", "string"]},
        "limit": {"type": ["number", "string"]},
        "action": {"type": "string"},
        "detail": {"type": "string"},
        "applicable": {"type": "boolean"},
    },
}

VALIDATION_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["results", "converged", "passedAll", "failed"],
    "additionalProperties": True,
    "properties": {
        "results": {
            "type": "array",
            "items": CONSTRAINT_CHECK_SCHEMA,
            "minItems": 10,
            "maxItems": 10,
        },
        "converged": {"type": "boolean"},
        "iterations": {"type": "integer", "minimum": 0},
        "passedAll": {"type": "boolean"},
        "failed": {"type": "array", "items": {"type": "string"}},
        "final_params": {"type": "object"},
        "history": {"type": "array"},
    },
}

PROCESS_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "cardId",
        "partName",
        "materialCode",
        "machineCode",
        "toolCode",
        "spindleSpeed",
        "feedRate",
        "feedPerTooth",
        "axialDepth",
        "radialWidth",
        "coolant",
        "status",
    ],
    "additionalProperties": True,
    "properties": {
        "cardId": {"type": "string", "pattern": "^PC-\\d{8}-\\d{4}$"},
        "partName": {"type": "string"},
        "materialCode": {"type": "string", "pattern": "^M[1-5]$"},
        "blankSize": {"type": "string"},
        "opNo": {"type": "integer", "minimum": 1},
        "opName": {"type": "string"},
        "machineCode": {"type": "string"},
        "toolCode": {"type": "string", "pattern": "^T[1-6]$"},
        "spindleSpeed": {"type": "number", "minimum": 0, "maximum": 8000},
        "feedRate": {"type": "number", "minimum": 0, "maximum": 10000},
        "feedPerTooth": {"type": "number", "minimum": 0},
        "axialDepth": {"type": "number", "minimum": 0},
        "radialWidth": {"type": "number", "minimum": 0},
        "coolant": {"type": "string"},
        "status": {"type": "string", "enum": ["DRAFT", "ISSUED", "LOCKED", "SUPERSEDED"]},
        "revision": {"type": "integer", "minimum": 1},
        "contentHash": {"type": "string"},
    },
}

CLOSURE_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["trace_id", "card_id", "material_code", "tool_code"],
    "additionalProperties": True,
    "properties": {
        "trace_id": {"type": "string", "pattern": "^TR-\\d{8}-\\d{4}$"},
        "card_id": {"type": "string", "pattern": "^PC-\\d{8}-\\d{4}$"},
        "material_code": {"type": "string", "pattern": "^M[1-5]$"},
        "tool_code": {"type": "string", "pattern": "^T[1-6]$"},
        "actual_ra": {"type": ["number", "null"], "minimum": 0},
        "actual_time": {"type": ["number", "null"], "minimum": 0},
        "actual_life": {"type": ["number", "null"], "minimum": 0},
        "chatter_observed": {"type": "boolean"},
    },
}

REFUSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["refusalReason", "reason", "safeAction", "alternative", "escalateTo"],
    "additionalProperties": True,
    "properties": {
        "refusalReason": {"type": "string", "enum": REFUSAL_ENUM},
        "reason": {"type": "string"},
        "safeAction": {"type": "string"},
        "alternative": {"type": "string"},
        "escalateTo": {"type": "string"},
        "safetyCheck": {"type": "string"},
        "confidence": {"type": "string", "enum": CONFIDENCE_ENUM},
    },
}

PREDICTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ra", "life", "time"],
    "additionalProperties": False,
    "properties": {
        "ra": {"$ref": "#/definitions/interval"},
        "life": {"$ref": "#/definitions/interval"},
        "time": {"$ref": "#/definitions/interval"},
    },
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "ParameterRecommendation": PARAMETER_RECOMMENDATION_SCHEMA,
    "ConstraintCheckResult": CONSTRAINT_CHECK_SCHEMA,
    "ValidationReport": VALIDATION_REPORT_SCHEMA,
    "ProcessCard": PROCESS_CARD_SCHEMA,
    "ClosureRecord": CLOSURE_RECORD_SCHEMA,
    "Refusal": REFUSAL_SCHEMA,
}


def validate_named(name: str, instance: Any) -> None:
    """按名称校验。

    Args:
        name: Schema 名称。
        instance: 待校验数据。

    Raises:
        SchemaError: 名称未知或校验失败。
    """
    schema = SCHEMAS.get(name)
    if schema is None:
        raise SchemaError(f"未登记的 Schema 名称：{name}")
    validate_schema(instance, schema)
