"""服务层与 HTTP API 测试。

覆盖业务门面的七大用例、错误码映射、Schema 严格性、
LLM 降级行为、数值纪律检查，以及 HTTP 层全部端点与边界。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from typing import Any

from tests import ZrCutTestCase
from zrcut.api import ApiHandler, create_server
from zrcut.domain import Confidence
from zrcut.errors import ErrorCode, ZrCutError
from zrcut.llm import LLMConfig, LLMClient, check_numeric_discipline, load_prompt, render_template
from zrcut.schema import (
    REFUSAL_SCHEMA,
    SchemaError,
    validate_named,
    validate_schema,
)
from zrcut.service import ServiceConfig, ZrCutService


class TestServiceRecommend(ZrCutTestCase):
    """服务层推荐用例。"""

    def setUp(self) -> None:
        self.svc = ZrCutService()

    def test_returns_all_sections(self) -> None:
        r = self.svc.recommend(
            material="M1", tool="T3", operation="OP2", ra_req=1.6
        )
        for key in ("request", "primary", "alternatives", "validation",
                    "warnings", "knowledge", "narrative", "narrativeMode"):
            with self.subTest(key=key):
                self.assertIn(key, r)

    def test_unknown_material_maps_to_error_code(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.recommend(material="M9", tool="T3", operation="OP2")
        self.assertEqual(ctx.exception.code, ErrorCode.UNKNOWN_MATERIAL)

    def test_unknown_tool_maps_to_error_code(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.recommend(material="M1", tool="T9", operation="OP2")
        self.assertEqual(ctx.exception.code, ErrorCode.UNKNOWN_TOOL)

    def test_unknown_operation_maps_to_error_code(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.recommend(material="M1", tool="T3", operation="OP9")
        self.assertEqual(ctx.exception.code, ErrorCode.UNKNOWN_OPERATION)

    def test_tool_operation_mismatch_has_dedicated_code(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.recommend(material="M1", tool="T1", operation="OP1")
        self.assertEqual(ctx.exception.code, ErrorCode.TOOL_OPERATION_MISMATCH)

    def test_coolant_required_has_dedicated_code(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.recommend(
                material="M3", tool="T1", operation="OP2",
                ra_req=0.8, coolant_available=False,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.COOLANT_REQUIRED)
        self.assertIn("safeAction", ctx.exception.detail)

    def test_narrative_falls_back_to_template(self) -> None:
        """无 API Key 时必须降级为模板叙述而非报错。"""
        r = self.svc.recommend(material="M1", tool="T3", operation="OP2")
        self.assertEqual(r["narrativeMode"], "template")
        self.assertTrue(r["narrative"])
        self.assertIn("结论", r["narrative"])

    def test_knowledge_attached(self) -> None:
        r = self.svc.recommend(material="M1", tool="T3", operation="OP2")
        self.assertTrue(r["knowledge"])
        self.assertIn("kbId", r["knowledge"][0])

    def test_frozen_combination_blocked(self) -> None:
        """回填严重偏差后该组合必须停用自动推荐。"""
        svc = ZrCutService()
        svc.record_closure(
            trace_id="TR-20261109-0001", card_id="PC-20261109-0001",
            material="M1", tool="T3",
            predicted_ra=1.0, actual_ra=3.0,
            predicted_time=20.0, actual_time=20.0,
            predicted_life=15.0, actual_life=15.0,
        )
        with self.assertRaises(ZrCutError) as ctx:
            svc.recommend(material="M1", tool="T3", operation="OP2")
        self.assertEqual(ctx.exception.code, ErrorCode.CONSTRAINT_VIOLATED)


class TestServiceValidate(ZrCutTestCase):
    """服务层校验用例。"""

    def setUp(self) -> None:
        self.svc = ZrCutService()

    def test_returns_ten_checks(self) -> None:
        v = self.svc.validate_params(
            material="M1", tool="T3", vc=168, fz=0.12, ap=8, ae=4, ra_req=1.6
        )
        self.assertEqual(len(v["results"]), 10)

    def test_converges_overrun(self) -> None:
        v = self.svc.validate_params(
            material="M1", tool="T4", vc=180, fz=0.12, ap=20, ae=14, ra_req=1.6
        )
        self.assertTrue(v["converged"])
        self.assertEqual(v["failed"], [])

    def test_no_converge_returns_raw_failures(self) -> None:
        v = self.svc.validate_params(
            material="M1", tool="T4", vc=180, fz=0.12, ap=20, ae=14,
            ra_req=1.6, converge_attempt=False,
        )
        self.assertFalse(v["converged"])
        self.assertTrue(v["failed"])

    def test_rejects_non_positive(self) -> None:
        """任一参数非正都必须拒绝。"""
        base = dict(material="M1", tool="T3", vc=168.0, fz=0.12, ap=8.0, ae=4.0)
        for field in ("vc", "fz", "ap", "ae"):
            with self.subTest(field=field):
                kwargs = dict(base)
                kwargs[field] = 0.0
                with self.assertRaises(ZrCutError):
                    self.svc.validate_params(**kwargs)

    def test_final_metrics_present(self) -> None:
        v = self.svc.validate_params(
            material="M1", tool="T3", vc=168, fz=0.12, ap=8, ae=4, ra_req=1.6
        )
        for key in ("n", "vf", "mrr", "pc", "raPred", "toolLife"):
            self.assertIn(key, v["finalMetrics"])


class TestServicePredict(ZrCutTestCase):
    """服务层预测用例。"""

    def setUp(self) -> None:
        self.svc = ZrCutService()

    def test_returns_three_intervals(self) -> None:
        p = self.svc.predict(material="M1", tool="T3", vc=168, fz=0.12, ap=8)
        for key in ("ra", "life", "time"):
            with self.subTest(metric=key):
                self.assertIn(key, p)
                self.assertIn("value", p[key])

    def test_no_training_data_low_confidence(self) -> None:
        p = self.svc.predict(material="M1", tool="T3", vc=168, fz=0.12, ap=8)
        self.assertEqual(p["ra"]["confidence"], "LOW")

    def test_metrics_included(self) -> None:
        p = self.svc.predict(material="M1", tool="T3", vc=168, fz=0.12, ap=8)
        self.assertIn("metrics", p)
        self.assertIn("raTh", p["metrics"])


class TestServiceCardAndClosure(ZrCutTestCase):
    """服务层工艺卡与回填用例。"""

    def setUp(self) -> None:
        self.svc = ZrCutService()

    def test_create_and_issue(self) -> None:
        c = self.svc.create_card(
            part_name="底板", blank_size="100x60x20", op_no=20,
            material="M1", tool="T3", operation="OP2", ra_req=1.6,
        )
        self.assertEqual(c["status"], "DRAFT")
        issued = self.svc.issue_card(c["cardId"], issued_by="工艺-张")
        self.assertEqual(issued["status"], "LOCKED")

    def test_missing_slot_raises(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.create_card(part_name="底板", blank_size="1x1x1")
        self.assertEqual(ctx.exception.code, ErrorCode.MISSING_SLOT)

    def test_duplicate_card_id_rejected(self) -> None:
        kwargs = dict(
            part_name="底板", blank_size="100x60x20", op_no=20,
            material="M1", tool="T3", operation="OP2", card_id="PC-20261109-0001",
        )
        self.svc.create_card(**kwargs)
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.create_card(**kwargs)
        self.assertEqual(ctx.exception.code, ErrorCode.CARD_INVALID)

    def test_unknown_card_404(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            self.svc.get_card("PC-20261109-9999")
        self.assertEqual(ctx.exception.code, ErrorCode.CARD_NOT_FOUND)
        self.assertEqual(ctx.exception.http_status, 404)

    def test_closure_inherits_prediction_from_card(self) -> None:
        c = self.svc.create_card(
            part_name="底板", blank_size="100x60x20", op_no=20,
            material="M1", tool="T3", operation="OP2", ra_req=1.6,
        )
        out = self.svc.record_closure(
            trace_id="TR-20261109-0001", card_id=c["cardId"],
            material="M1", tool="T3", actual_ra=1.0, actual_time=1.5,
        )
        self.assertIsNotNone(out["record"]["predicted_ra"])
        self.assertIn("assessment", out)

    def test_closure_schema_validated(self) -> None:
        with self.assertRaises(ZrCutError):
            self.svc.record_closure(
                trace_id="BAD-ID", card_id="PC-20261109-0001",
                material="M1", tool="T3", actual_ra=1.0,
            )


class TestServiceSearch(ZrCutTestCase):
    """服务层检索用例。"""

    def setUp(self) -> None:
        self.svc = ZrCutService()

    def test_hit(self) -> None:
        s = self.svc.search("304 不锈钢 内冷")
        self.assertFalse(s["empty"])
        self.assertTrue(s["hits"])

    def test_empty_has_message(self) -> None:
        s = self.svc.search("火星探测器轨道参数")
        self.assertTrue(s["empty"])
        self.assertIn("未检索到", s["emptyMessage"])


class TestNumericDiscipline(ZrCutTestCase):
    """数值纪律检查器。"""

    def test_accepts_values_from_tool(self) -> None:
        text = "推荐 n=3462 r/min，Pc=6.68 kW"
        self.assertEqual(check_numeric_discipline(text, [3462.0, 6.68]), [])

    def test_flags_invented_value(self) -> None:
        text = "推荐 n=9999 r/min"
        self.assertTrue(check_numeric_discipline(text, [3462.0]))

    def test_tolerance_allows_rounding(self) -> None:
        """±2% 内的取整不应被误判。"""
        self.assertEqual(check_numeric_discipline("n=3500", [3462.0]), [])

    def test_ignores_small_integers(self) -> None:
        """档位与序号类小整数不参与判定。"""
        self.assertEqual(check_numeric_discipline("第 3 档，共 5 条", [3462.0]), [])


class TestPromptLoading(ZrCutTestCase):
    """提示词资产加载与渲染。"""

    def test_loads_sys_prompt(self) -> None:
        text = load_prompt("ZRCNC-SYS-001")
        self.assertIn("智刃工艺", text)
        self.assertGreater(len(text), 200)

    def test_missing_prompt_raises(self) -> None:
        with self.assertRaises(ZrCutError):
            load_prompt("ZRCNC-NOPE-999")

    def test_render_replaces_variables(self) -> None:
        out = render_template("角色={{role}} 机床={{machine_code}}", {"role": "operator", "machine_code": "EQ-A01-0037"})
        self.assertIn("operator", out)
        self.assertIn("EQ-A01-0037", out)

    def test_missing_variable_becomes_unknown(self) -> None:
        out = render_template("x={{missing}}", {})
        self.assertIn("未知", out)


class TestLLMDegradation(ZrCutTestCase):
    """LLM 无 Key 时的优雅降级。"""

    def test_disabled_without_key(self) -> None:
        cfg = LLMConfig(api_key="")
        self.assertFalse(cfg.enabled)

    def test_complete_returns_template_mode(self) -> None:
        client = LLMClient(LLMConfig(api_key=""))
        result = client.complete("system", "user")
        self.assertEqual(result.mode, "template")
        self.assertTrue(result.degraded_reason)

    def test_unreachable_endpoint_degrades_not_raises(self) -> None:
        """指向不可达地址时必须降级返回，不得抛异常中断业务。"""
        cfg = LLMConfig(
            api_key="dummy",
            base_url="http://127.0.0.1:1",
            timeout_s=1.0,
        )
        client = LLMClient(cfg)
        result = client.complete("system", "user")
        self.assertEqual(result.mode, "template")


class TestSchemaValidation(ZrCutTestCase):
    """Schema 校验器。"""

    def test_refusal_schema_accepts_valid(self) -> None:
        validate_schema(
            {
                "refusalReason": "MACHINE_LIMIT",
                "reason": "超主轴上限",
                "safeAction": "停机",
                "alternative": "降至 8000 以下",
                "escalateTo": "process_engineer",
            },
            REFUSAL_SCHEMA,
        )

    def test_refusal_schema_rejects_bad_enum(self) -> None:
        with self.assertRaises(SchemaError):
            validate_schema(
                {
                    "refusalReason": "NOT_A_CODE",
                    "reason": "x",
                    "safeAction": "y",
                    "alternative": "z",
                    "escalateTo": "w",
                },
                REFUSAL_SCHEMA,
            )

    def test_union_type_accepts_null(self) -> None:
        """联合类型必须支持 null，这是可选数值字段的常见形态。"""
        validate_schema(
            {"label": "P0", "strategy": "LIFE", "vc": 1, "fz": 1, "ap": 1,
             "ae": 1, "n": 1, "vf": 1, "mrr": 1, "pc": 1, "raTh": 1,
             "raPred": 1, "toolLife": 1, "machiningTime": None,
             "chatterRisk": "LOW", "confidence": "HIGH"},
            __import__("zrcut.schema", fromlist=["PLAN_SCHEMA"]).PLAN_SCHEMA,
        )

    def test_unknown_schema_name_raises(self) -> None:
        with self.assertRaises(SchemaError):
            validate_named("NoSuchSchema", {})


class TestApiHandlerMeta(ZrCutTestCase):
    """API 元数据。"""

    def test_meta_covers_all_entities(self) -> None:
        meta = ApiHandler._meta()
        self.assertEqual(len(meta["materials"]), 5)
        self.assertEqual(len(meta["tools"]), 6)
        self.assertEqual(len(meta["operations"]), 4)
        self.assertEqual(meta["machine"]["spindleMaxRpm"], 8000.0)

    def test_pick_filters_unknown_fields(self) -> None:
        picked = ApiHandler._pick(
            {"material": "M1", "evil": "x", "raReq": 1.6}, "recommend"
        )
        self.assertNotIn("evil", picked)
        self.assertEqual(picked["ra_req"], 1.6)

    def test_pick_renames_camel_to_snake(self) -> None:
        picked = ApiHandler._pick({"partName": "A", "blankSize": "B"}, "card")
        self.assertIn("part_name", picked)
        self.assertIn("blank_size", picked)


class TestHttpApi(ZrCutTestCase):
    """HTTP 端到端。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(port=0)
        cls.host, cls.port = cls.server.server_address[:2]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{cls.host}:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health(self) -> None:
        status, payload = self._call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["status"], "healthy")

    def test_meta(self) -> None:
        status, payload = self._call("GET", "/api/v1/meta")
        self.assertEqual(status, 200)
        self.assertIn("materials", payload["data"])

    def test_recommend(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/recommend",
            {"material": "M1", "tool": "T3", "operation": "OP2", "raReq": 1.6},
        )
        self.assertEqual(status, 200)
        self.assertIn("primary", payload["data"])

    def test_validate(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/validate",
            {"material": "M1", "tool": "T4", "vc": 180, "fz": 0.12,
             "ap": 20, "ae": 14, "raReq": 1.6},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["converged"])

    def test_predict(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/predict",
            {"material": "M1", "tool": "T3", "vc": 168, "fz": 0.12, "ap": 8},
        )
        self.assertEqual(status, 200)
        self.assertIn("ra", payload["data"])

    def test_card_lifecycle(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/cards",
            {"partName": "底板", "blankSize": "100x60x20", "opNo": 20,
             "material": "M1", "tool": "T3", "operation": "OP2", "raReq": 1.6},
        )
        self.assertEqual(status, 201)
        cid = payload["data"]["cardId"]

        status, payload = self._call("GET", f"/api/v1/cards/{cid}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["status"], "DRAFT")

        status, payload = self._call(
            "POST", f"/api/v1/cards/{cid}/issue", {"issuedBy": "工艺-张"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["status"], "LOCKED")

    def test_issue_requires_issuer(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/cards", 
            {"partName": "A", "blankSize": "1x1x1", "opNo": 10,
             "material": "M1", "tool": "T3", "operation": "OP2"},
        )
        cid = payload["data"]["cardId"]
        status, payload = self._call("POST", f"/api/v1/cards/{cid}/issue", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "E1001")

    def test_closure_and_stats(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/closures",
            {"traceId": "TR-20261109-0777", "cardId": "PC-20261109-0001",
             "material": "M1", "tool": "T3", "actualRa": 1.05,
             "actualTime": 1.5, "actualLife": 20.0,
             "predictedRa": 1.0, "predictedTime": 1.6, "predictedLife": 20.0},
        )
        self.assertEqual(status, 201)
        status, payload = self._call("GET", "/api/v1/closures/stats?material=M1&tool=T3")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["data"]["nSamples"], 1)

    def test_search_with_chinese_query(self) -> None:
        from urllib.parse import quote

        status, payload = self._call(
            "GET", f"/api/v1/search?q={quote('304 不锈钢 内冷')}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["hits"])

    def test_unknown_route_404(self) -> None:
        status, payload = self._call("GET", "/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "E1001")

    def test_unknown_material_error_code(self) -> None:
        status, payload = self._call(
            "POST", "/api/v1/recommend",
            {"material": "M9", "tool": "T3", "operation": "OP2"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "E1003")

    def test_missing_card_404(self) -> None:
        status, payload = self._call("GET", "/api/v1/cards/PC-20261109-9999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "E5001")

    def test_invalid_json_body(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/v1/recommend",
            data=b"not-json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.status
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read())
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "E1001")


class TestConfigDefaults(ZrCutTestCase):
    """默认配置合理性。"""

    def test_service_defaults(self) -> None:
        cfg = ServiceConfig()
        self.assertFalse(cfg.enable_llm)
        self.assertTrue(cfg.strict_schema)
        self.assertTrue(cfg.enable_numeric_check)

    def test_service_has_kb_loaded(self) -> None:
        self.assertGreaterEqual(len(ZrCutService().kb), 10)
