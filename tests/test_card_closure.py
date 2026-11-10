"""工艺卡与回填闭环测试。

覆盖工艺卡 14 必填字段、签发锁定、修订变更单、
内容哈希防篡改、扫码载荷，以及回填三档判定与离线训练准入。
"""

from __future__ import annotations

from datetime import date

from tests import ZrCutTestCase
from zrcut.card import (
    CardStatus,
    ProcessCard,
    build_card,
    next_card_id,
    verify_card,
)
from zrcut.closure import (
    ClosureRecord,
    ClosureStore,
    DeviationLevel,
    Disposition,
    assess_closure,
    relative_deviation,
    retrain,
)
from zrcut.domain import MaterialId, OperationId, ToolId
from zrcut.errors import ErrorCode, ZrCutError
from zrcut.recommender import RecommendationRequest, recommend


def _rec(**kw):
    """构造推荐结果供工艺卡使用。"""
    defaults = dict(
        material=MaterialId.M1,
        tool=ToolId.T3,
        operation=OperationId.OP2,
        ra_req=1.6,
        volume=61200.0,
    )
    defaults.update(kw)
    return recommend(RecommendationRequest(**defaults))


def _card(**kw) -> ProcessCard:
    """构造默认工艺卡。"""
    defaults = dict(
        card_id="PC-20261109-0001",
        part_name="底板",
        material_code="M1",
        blank_size="100x60x20",
        op_no=20,
        op_name="侧铣",
        machine_code="EQ-A01-0037",
        tool_code="T3",
        spindle_speed=3462.0,
        feed_rate=1565.0,
        feed_per_tooth=0.113,
        axial_depth=16.0,
        radial_width=8.0,
        coolant="乳化液外冷",
    )
    defaults.update(kw)
    return ProcessCard(**defaults)


class TestCardIdGeneration(ZrCutTestCase):
    """卡号生成与格式。"""

    def test_format_matches_pattern(self) -> None:
        cid = next_card_id(date(2026, 11, 9))
        self.assertRegex(cid, r"^PC-20261109-\d{4}$")

    def test_increments_within_same_day(self) -> None:
        a = next_card_id(date(2030, 1, 1))
        b = next_card_id(date(2030, 1, 1))
        self.assertNotEqual(a, b)
        self.assertLess(a, b)

    def test_rejects_bad_card_id(self) -> None:
        with self.assertRaises(ZrCutError) as ctx:
            _card(card_id="PC-0001")
        self.assertEqual(ctx.exception.code, ErrorCode.CARD_INVALID)


class TestCardFields(ZrCutTestCase):
    """14 必填字段与校验。"""

    def test_all_required_fields_present(self) -> None:
        """工艺卡必须输出全部必填字段。"""
        d = _card().to_dict()
        required = [
            "cardId", "partName", "materialCode", "blankSize", "opNo", "opName",
            "machineCode", "toolCode", "spindleSpeed", "feedRate", "feedPerTooth",
            "axialDepth", "radialWidth", "coolant",
        ]
        for key in required:
            with self.subTest(field=key):
                self.assertIn(key, d)
                self.assertIsNotNone(d[key])

    def test_annotation_fields_present(self) -> None:
        """4 个附注字段必须存在。"""
        d = _card().to_dict()
        for key in ("estMachiningTime", "estRoughness", "chatterRisk", "firstArticleReq"):
            with self.subTest(field=key):
                self.assertIn(key, d)

    def test_rejects_non_positive_parameters(self) -> None:
        for field in ("spindle_speed", "feed_rate", "feed_per_tooth",
                      "axial_depth", "radial_width"):
            with self.subTest(field=field):
                with self.assertRaises(ZrCutError):
                    _card(**{field: 0.0})


class TestCardLocking(ZrCutTestCase):
    """签发与锁定。"""

    def test_issue_locks_card(self) -> None:
        card = _card()
        self.assertIs(card.status, CardStatus.DRAFT)
        card.issue("工艺-张")
        self.assertIs(card.status, CardStatus.LOCKED)
        self.assertEqual(card.issued_by, "工艺-张")
        self.assertIsNotNone(card.issued_at)

    def test_double_issue_rejected(self) -> None:
        """已签发不得重复签发。"""
        card = _card()
        card.issue("工艺-张")
        with self.assertRaises(ZrCutError) as ctx:
            card.issue("工艺-李")
        self.assertEqual(ctx.exception.code, ErrorCode.CARD_LOCKED)

    def test_revise_creates_new_revision(self) -> None:
        """修订必须产生新版本并把原卡标记为被替代。"""
        card = _card()
        card.issue("工艺-张")
        new = card.revise({"spindle_speed": 3000.0}, reason="首件振纹，降速")
        self.assertEqual(new.revision, 2)
        self.assertEqual(new.supersedes, card.card_id)
        self.assertIs(new.status, CardStatus.DRAFT)
        self.assertIs(card.status, CardStatus.SUPERSEDED)

    def test_revise_requires_reason(self) -> None:
        with self.assertRaises(ZrCutError):
            _card().revise({"spindle_speed": 3000.0}, reason="")

    def test_revise_cannot_change_card_id(self) -> None:
        with self.assertRaises(ZrCutError):
            _card().revise({"card_id": "PC-20261109-9999"}, reason="尝试改号")

    def test_revise_resets_issue_fields(self) -> None:
        card = _card()
        card.issue("工艺-张")
        new = card.revise({"axial_depth": 12.0}, reason="降切深")
        self.assertIsNone(new.issued_by)
        self.assertIsNone(new.issued_at)
        self.assertIn("修订原因：降切深", new.notes)


class TestCardHash(ZrCutTestCase):
    """内容哈希与防篡改。"""

    def test_hash_is_stable_for_same_content(self) -> None:
        a = _card()
        b = _card()
        self.assertEqual(a.content_hash, b.content_hash)

    def test_hash_changes_with_parameter(self) -> None:
        a = _card()
        b = _card(spindle_speed=3463.0)
        self.assertNotEqual(a.content_hash, b.content_hash)

    def test_verify_detects_tampering(self) -> None:
        card = _card()
        original = card.content_hash
        self.assertTrue(verify_card(card, original))
        # 篡改参数后旧哈希不再匹配
        tampered = _card(spindle_speed=9999.0)
        self.assertFalse(verify_card(tampered, original))


class TestCardExports(ZrCutTestCase):
    """三种导出形态。"""

    def test_markdown_contains_key_fields(self) -> None:
        md = _card().to_markdown()
        self.assertIn("工艺卡", md)
        self.assertIn("主轴转速", md)
        self.assertIn("内容哈希", md)

    def test_qr_payload_format(self) -> None:
        """扫码载荷必须是紧凑可解析的键值串。"""
        qr = _card().qr_payload()
        parts = qr.split("|")
        self.assertEqual(parts[0], "ZRCUT")
        self.assertIn("n=", qr)
        self.assertIn("vf=", qr)
        self.assertIn("rev=", qr)

    def test_json_dict_has_hash(self) -> None:
        self.assertIn("contentHash", _card().to_dict())


class TestBuildCardFromRecommendation(ZrCutTestCase):
    """由推荐结果生成工艺卡。"""

    def test_builds_with_correct_parameters(self) -> None:
        rec = _rec()
        card = build_card(rec, part_name="支架", blank_size="130x90x35", op_no=20)
        self.assertAlmostEqualRel(card.spindle_speed, rec.primary.n, 1e-3)
        self.assertAlmostEqualRel(card.feed_rate, rec.primary.vf, 1e-3)
        self.assertAlmostEqualRel(card.axial_depth, rec.primary.ap, 1e-6)

    def test_copies_warnings_into_notes(self) -> None:
        rec = _rec()
        card = build_card(rec, part_name="支架", blank_size="130x90x35", op_no=20)
        if rec.warnings:
            self.assertTrue(card.notes)

    def test_chatter_drives_first_article_requirement(self) -> None:
        rec = _rec()
        card = build_card(rec, part_name="支架", blank_size="130x90x35", op_no=20)
        if rec.primary.chatter_risk != "LOW":
            self.assertIn("异响", card.first_article_req)


class TestRelativeDeviation(ZrCutTestCase):
    """偏差计算。"""

    def test_positive_when_predicted_higher(self) -> None:
        self.assertAlmostEqualRel(relative_deviation(1.2, 1.0), 0.2, 1e-9)

    def test_none_when_missing(self) -> None:
        self.assertIsNone(relative_deviation(None, 1.0))
        self.assertIsNone(relative_deviation(1.0, None))

    def test_zero_actual_raises(self) -> None:
        with self.assertRaises(ZrCutError):
            relative_deviation(1.0, 0.0)


class TestClosureAssessment(ZrCutTestCase):
    """回填三档判定。"""

    def _rec_cl(self, **kw) -> ClosureRecord:
        defaults = dict(
            trace_id="TR-20261109-0001",
            card_id="PC-20261109-0001",
            material_code="M1",
            tool_code="T3",
            predicted_ra=1.0,
            actual_ra=1.0,
            predicted_time=20.0,
            actual_time=20.0,
            predicted_life=15.0,
            actual_life=15.0,
        )
        defaults.update(kw)
        return ClosureRecord(**defaults)

    def test_normal_when_within_tolerance(self) -> None:
        a = assess_closure(self._rec_cl(actual_ra=1.1, actual_time=21.0, actual_life=14.8))
        self.assertIs(a.level, DeviationLevel.NORMAL)
        self.assertIs(a.disposition, Disposition.RECORD_ONLY)

    def test_medium_when_single_trigger(self) -> None:
        """Ra 偏差超 25% 但未超两倍，应判中档。"""
        a = assess_closure(self._rec_cl(actual_ra=1.40))
        self.assertIs(a.level, DeviationLevel.MEDIUM)
        self.assertIs(a.disposition, Disposition.ADJUST_HINT)
        self.assertIn("RA", a.triggers)

    def test_severe_when_multiple_triggers(self) -> None:
        a = assess_closure(
            self._rec_cl(actual_ra=2.1, actual_time=40.0, actual_life=6.0)
        )
        self.assertIs(a.level, DeviationLevel.SEVERE)
        self.assertIs(a.disposition, Disposition.FREEZE_AND_ESCALATE)

    def test_severe_when_single_doubles_threshold(self) -> None:
        """单项偏差超阈值两倍也应判严重。"""
        a = assess_closure(self._rec_cl(actual_ra=3.0))
        self.assertIs(a.level, DeviationLevel.SEVERE)

    def test_chatter_observation_escalates(self) -> None:
        """现场观察到颤振时必须升级处置。"""
        a = assess_closure(self._rec_cl(chatter_observed=True))
        self.assertIs(a.level, DeviationLevel.SEVERE)

    def test_hint_direction_for_worse_roughness(self) -> None:
        """实测粗糙度劣于预测时，提示必须建议降 fz。"""
        a = assess_closure(self._rec_cl(actual_ra=1.40))
        self.assertIn("fz", a.hint)

    def test_hint_direction_for_better_roughness(self) -> None:
        a = assess_closure(self._rec_cl(actual_ra=0.70))
        self.assertIn("优于", a.hint)

    def test_hint_direction_for_short_life(self) -> None:
        """实测寿命短于预测时，提示必须建议降 vc。"""
        a = assess_closure(self._rec_cl(actual_life=9.0))
        self.assertIn("vc", a.hint)

    def test_confidence_reflects_level(self) -> None:
        normal = assess_closure(self._rec_cl(actual_ra=1.05))
        severe = assess_closure(self._rec_cl(actual_ra=3.0))
        self.assertEqual(normal.confidence_after.value, "HIGH")
        self.assertEqual(severe.confidence_after.value, "LOW")

    def test_to_dict_serializable(self) -> None:
        d = assess_closure(self._rec_cl(actual_ra=1.40)).to_dict()
        self.assertIn("level", d)
        self.assertIn("triggers", d)
        self.assertIn("hint", d)

    def test_rejects_bad_trace_id(self) -> None:
        with self.assertRaises(ZrCutError):
            self._rec_cl(trace_id="TR-0001")

    def test_rejects_non_positive_actual(self) -> None:
        with self.assertRaises(ZrCutError):
            self._rec_cl(actual_ra=0.0)


class TestClosureStore(ZrCutTestCase):
    """回填仓库与训练准入。"""

    def _record(self, trace: str, **kw) -> ClosureRecord:
        defaults = dict(
            trace_id=trace,
            card_id="PC-20261109-0001",
            material_code="M1",
            tool_code="T3",
            predicted_ra=1.0,
            actual_ra=1.0,
            predicted_time=20.0,
            actual_time=20.0,
            predicted_life=15.0,
            actual_life=15.0,
        )
        defaults.update(kw)
        return ClosureRecord(**defaults)

    def test_add_and_count(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001"))
        self.assertEqual(len(store.records), 1)

    def test_duplicate_trace_id_rejected(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001"))
        with self.assertRaises(ZrCutError):
            store.add(self._record("TR-20261109-0001"))

    def test_severe_freezes_combination(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001", actual_ra=3.0))
        self.assertTrue(store.is_frozen("M1", "T3"))
        self.assertFalse(store.is_frozen("M1", "T4"))

    def test_stats_on_empty(self) -> None:
        self.assertEqual(ClosureStore().stats()["nSamples"], 0)

    def test_stats_aggregates(self) -> None:
        store = ClosureStore()
        for i, ra in enumerate([1.05, 1.10, 1.15], start=1):
            store.add(self._record(f"TR-20261109-{i:04d}", actual_ra=ra))
        st = store.stats("M1", "T3")
        self.assertEqual(st["nSamples"], 3)
        self.assertIn("levelDistribution", st)
        self.assertIsNotNone(st["raDeviationMean"])

    def test_retrain_blocked_when_frozen(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001", actual_ra=3.0))
        r = retrain(store, material_code="M1", tool_code="T3")
        self.assertFalse(r["eligible"])
        self.assertEqual(r["nextAction"], "FREEZE_BLOCKED")

    def test_retrain_needs_enough_samples(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001"))
        r = retrain(store, material_code="M1", tool_code="T3", min_samples=3)
        self.assertFalse(r["eligible"])
        self.assertEqual(r["nextAction"], "COLLECT_MORE")

    def test_retrain_eligible_with_enough_clean_samples(self) -> None:
        store = ClosureStore()
        for i in range(3):
            store.add(self._record(f"TR-20261109-{i:04d}", actual_ra=1.05))
        r = retrain(store, material_code="M1", tool_code="T3", min_samples=3)
        self.assertTrue(r["eligible"])
        self.assertEqual(r["nextAction"], "OFFLINE_TRAIN")

    def test_export_jsonl(self) -> None:
        store = ClosureStore()
        store.add(self._record("TR-20261109-0001"))
        lines = store.to_jsonl().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("trace_id", lines[0])
