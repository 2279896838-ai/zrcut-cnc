"""HTTP API 服务，仅依赖标准库 http.server。

接口清单：
    GET  /health                 健康检查
    GET  /api/v1/meta            材料、刀具、工序、机床元数据
    POST /api/v1/recommend       参数推荐
    POST /api/v1/validate        硬约束校验与收敛
    POST /api/v1/predict         三项预测与置信区间
    POST /api/v1/cards           生成工艺卡
    GET  /api/v1/cards/{id}      读取工艺卡
    POST /api/v1/cards/{id}/issue 签发锁定
    POST /api/v1/closures        写入回填
    GET  /api/v1/closures/stats  回填统计
    GET  /api/v1/search          知识检索

统一响应信封：{"ok": true, "data": ...} 或 {"ok": false, "error": {...}}
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .domain import MACHINE, MATERIALS, OPERATIONS, TOOLS
from .errors import ErrorCode, ZrCutError
from .service import ServiceConfig, ZrCutService

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000


class ApiHandler(BaseHTTPRequestHandler):
    """API 请求处理器。

    Attributes:
        service: 注入的服务实例。
        version: 版本号。
    """

    service: ZrCutService
    version: str = "1.0.0"
    server_version = "ZrCut/1.0"

    # ------------------------------------------------------------- 基础设施
    def log_message(self, fmt: str, *args: Any) -> None:
        """接管默认日志，统一走 logging。"""
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        """发送 JSON 响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data: Any, status: int = 200) -> None:
        """发送成功响应。"""
        self._send(status, {"ok": True, "data": data})

    def _fail(self, exc: ZrCutError) -> None:
        """发送业务错误响应。"""
        self._send(exc.http_status, {"ok": False, "error": exc.to_dict()["error"]})

    def _read_json(self) -> dict[str, Any]:
        """读取并解析 JSON 请求体。

        Returns:
            请求体字典。

        Raises:
            ZrCutError: 请求体过大或不是合法 JSON 对象。
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ZrCutError(
                ErrorCode.INVALID_INPUT,
                f"请求体过大：{length} 字节，上限 {MAX_BODY_BYTES}",
                {},
                413,
            )
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ZrCutError(
                ErrorCode.INVALID_INPUT, f"请求体不是合法 JSON：{exc}", {}, 400
            ) from exc
        if not isinstance(data, dict):
            raise ZrCutError(
                ErrorCode.INVALID_INPUT, "请求体必须是 JSON 对象", {}, 400
            )
        return data

    # ------------------------------------------------------------- 路由
    def do_GET(self) -> None:
        """处理 GET。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/health":
                self._ok(
                    {
                        "status": "healthy",
                        "version": self.version,
                        "kbEntries": len(self.service.kb),
                        "cards": len(self.service.cards),
                        "llmEnabled": self.service.config.enable_llm,
                    }
                )
            elif path == "/api/v1/meta":
                self._ok(self._meta())
            elif path.startswith("/api/v1/cards/"):
                card_id = path.split("/")[-1]
                self._ok(self.service.get_card(card_id))
            elif path == "/api/v1/closures/stats":
                qs = parse_qs(parsed.query)
                material = (qs.get("material") or [None])[0]
                tool = (qs.get("tool") or [None])[0]
                self._ok(self.service.closure_stats(material=material, tool=tool))
            elif path == "/api/v1/search":
                qs = parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0]
                top_k = int((qs.get("topK") or ["5"])[0])
                self._ok(self.service.search(query, top_k=top_k))
            elif path == "/api/v1/cards":
                self._ok([c.to_dict() for c in self.service.cards.values()])
            else:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error": {
                            "code": "E1001",
                            "message": f"未找到接口：{path}",
                            "detail": {},
                        },
                    },
                )
        except ZrCutError as exc:
            self._fail(exc)
        except Exception as exc:  # noqa: BLE001 - 兜底防止进程崩溃
            logger.exception("GET 未捕获异常 path=%s", path)
            self._fail(
                ZrCutError(
                    ErrorCode.INTERNAL_ERROR,
                    f"服务器内部错误：{type(exc).__name__}",
                    {},
                    500,
                )
            )

    def do_POST(self) -> None:
        """处理 POST。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._read_json()
            if path == "/api/v1/recommend":
                self._ok(self.service.recommend(**self._pick(body, "recommend")))
            elif path == "/api/v1/validate":
                self._ok(self.service.validate_params(**self._pick(body, "validate")))
            elif path == "/api/v1/predict":
                self._ok(self.service.predict(**self._pick(body, "predict")))
            elif path == "/api/v1/cards":
                self._ok(self.service.create_card(**self._pick(body, "card")), 201)
            elif path.endswith("/issue") and path.startswith("/api/v1/cards/"):
                card_id = path.split("/")[-2]
                issued_by = str(body.get("issuedBy") or "")
                if not issued_by:
                    raise ZrCutError(
                        ErrorCode.INVALID_INPUT, "签发必须提供 issuedBy", {}, 400
                    )
                self._ok(self.service.issue_card(card_id, issued_by=issued_by))
            elif path == "/api/v1/closures":
                self._ok(self.service.record_closure(**self._pick(body, "closure")), 201)
            else:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error": {
                            "code": "E1001",
                            "message": f"未找到接口：{path}",
                            "detail": {},
                        },
                    },
                )
        except ZrCutError as exc:
            self._fail(exc)
        except TypeError as exc:
            self._fail(
                ZrCutError(
                    ErrorCode.INVALID_INPUT,
                    f"请求参数不匹配：{exc}",
                    {},
                    400,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST 未捕获异常 path=%s", path)
            self._fail(
                ZrCutError(
                    ErrorCode.INTERNAL_ERROR,
                    f"服务器内部错误：{type(exc).__name__}",
                    {},
                    500,
                )
            )

    # ------------------------------------------------------------- 数据整形
    @staticmethod
    def _pick(body: dict[str, Any], kind: str) -> dict[str, Any]:
        """从请求体提取目标函数所需的参数，屏蔽多余字段。

        Args:
            body: 原始请求体。
            kind: 接口类型。

        Returns:
            关键字参数字典。
        """
        allowed: dict[str, tuple[str, ...]] = {
            "recommend": (
                "material", "tool", "operation", "raReq", "strategy",
                "volume", "wallThickness", "coolantAvailable", "role",
            ),
            "validate": (
                "material", "tool", "vc", "fz", "ap", "ae", "raReq",
                "wallThickness", "coolantAvailable", "convergeAttempt",
            ),
            "predict": ("material", "tool", "vc", "fz", "ap", "volume"),
            "card": (
                "partName", "blankSize", "opNo", "material", "tool",
                "operation", "raReq", "volume", "wallThickness",
                "coolantAvailable", "cardId",
            ),
            "closure": (
                "traceId", "cardId", "material", "tool", "actualRa",
                "actualTime", "actualLife", "predictedRa", "predictedTime",
                "predictedLife", "toolWearVb", "chatterObserved", "anomaly",
                "operatorNote", "recordedBy",
            ),
        }
        ren = {
            "raReq": "ra_req",
            "wallThickness": "wall_thickness",
            "coolantAvailable": "coolant_available",
            "convergeAttempt": "converge_attempt",
            "partName": "part_name",
            "blankSize": "blank_size",
            "opNo": "op_no",
            "cardId": "card_id",
            "traceId": "trace_id",
            "actualRa": "actual_ra",
            "actualTime": "actual_time",
            "actualLife": "actual_life",
            "predictedRa": "predicted_ra",
            "predictedTime": "predicted_time",
            "predictedLife": "predicted_life",
            "toolWearVb": "tool_wear_vb",
            "chatterObserved": "chatter_observed",
            "operatorNote": "operator_note",
            "recordedBy": "recorded_by",
        }
        out: dict[str, Any] = {}
        for key in allowed.get(kind, ()):
            if key in body and body[key] is not None:
                out[ren.get(key, key)] = body[key]
        return out

    @staticmethod
    def _meta() -> dict[str, Any]:
        """返回元数据。"""
        return {
            "version": "1.0.0",
            "machine": {
                "code": MACHINE.code,
                "name": MACHINE.name,
                "controlSystem": MACHINE.control_system,
                "spindleMaxRpm": MACHINE.spindle_max_rpm,
                "powerContinuousKw": MACHINE.power_continuous_kw,
                "taper": MACHINE.taper,
                "naturalFrequencyHz": MACHINE.natural_frequency_hz,
            },
            "materials": [
                {
                    "code": m.code.value,
                    "name": m.name,
                    "grade": m.grade,
                    "hardness": m.hardness,
                    "vcRange": [m.vc_min, m.vc_max],
                    "fzRange": [m.fz_min, m.fz_max],
                    "requiresCoolant": m.requires_coolant,
                }
                for m in MATERIALS.values()
            ],
            "tools": [
                {
                    "code": t.code.value,
                    "kind": t.kind,
                    "diameter": t.diameter,
                    "teeth": t.teeth,
                    "overhangRatio": round(t.overhang_ratio, 3),
                }
                for t in TOOLS.values()
            ],
            "operations": [
                {"code": o.code.value, "name": o.name,
                 "typicalTools": [x.value for x in o.typical_tools]}
                for o in OPERATIONS.values()
            ],
        }


def create_server(
    host: str = "127.0.0.1",
    port: int = 8077,
    *,
    service: ZrCutService | None = None,
    config: ServiceConfig | None = None,
) -> ThreadingHTTPServer:
    """创建 HTTP 服务器实例（不启动）。

    Args:
        host: 绑定地址。
        port: 端口；0 表示由系统分配。
        service: 注入的服务；None 则新建。
        config: 服务配置。

    Returns:
        已配置好的 ThreadingHTTPServer。
    """
    svc = service or ZrCutService(config=config)
    handler = type(
        "BoundHandler",
        (ApiHandler,),
        {"service": svc, "version": "1.0.0"},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(host: str = "127.0.0.1", port: int = 8077, **kwargs: Any) -> None:
    """阻塞式启动服务。

    Args:
        host: 绑定地址。
        port: 端口。
        **kwargs: 传递给 create_server。
    """
    server = create_server(host, port, **kwargs)
    actual_host, actual_port = server.server_address[:2]
    logger.info("智刃工艺 API 已启动 http://%s:%s", actual_host, actual_port)
    print(f"智刃工艺 API 已启动：http://{actual_host}:{actual_port}")
    print(f"健康检查：http://{actual_host}:{actual_port}/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，正在关闭…")
    finally:
        server.shutdown()
        server.server_close()
