"""CLI 与评估体系测试。

CLI 通过直接调用 main(argv) 并捕获标准输出验证，避免子进程开销。
评估体系覆盖 400 条用例的构成、六指标计算与一票否决逻辑。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests import ZrCutTestCase
from zrcut.cli import _build_parser, main
from zrcut.evaluate import (
    EvalResult,
    TARGET_OVERRUN_MISS,
    TARGET_RA_MAE,
    TARGET_RECOMMEND_PASS,
    TARGET_REFUSAL_ACC,
    TARGET_SCHEMA_VALID,
    compute_metrics,
    eval_overrun,
    eval_prediction,
    eval_recommendation,
    eval_schema_refusal,
    run_builtin_eval,
)
from zrcut.service import ZrCutService


class TestCliParser(ZrCutTestCase):
    """CLI 参数解析器。"""

    def test_all_subcommands_registered(self) -> None:
        """全部子命令必须注册。"""
        p = _build_parser()
        for cmd in (
            "recommend", "validate", "predict", "card",
            "search", "meta", "eval", "serve",
        ):
            with self.subTest(cmd=cmd):
                # 通过解析最小合法参数确认子命令存在
                if cmd == "recommend":
                    p.parse_args([cmd, "-m", "M1", "-t", "T3", "-o", "OP2"])
                elif cmd == "validate":
                    p.parse_args([cmd, "-m", "M1", "-t", "T3", "--vc", "1",
                                  "--fz", "1", "--ap", "1", "--ae", "1"])
                elif cmd == "predict":
                    p.parse_args([cmd, "-m", "M1", "-t", "T3", "--vc", "1",
                                  "--fz", "1", "--ap", "1"])
                elif cmd == "card":
                    p.parse_args([cmd, "--part", "a", "--blank", "b",
                                  "-m", "M1", "-t", "T3", "-o", "OP2"])
                elif cmd == "search":
                    p.parse_args([cmd, "q"])
                else:
                    p.parse_args([cmd])

    def test_recommend_defaults(self) -> None:
        args = _build_parser().parse_args(
            ["recommend", "-m", "M1", "-t", "T3", "-o", "OP2"]
        )
        self.assertEqual(args.ra, 3.2)
        self.assertFalse(args.no_coolant)

    def test_validate_flags(self) -> None:
        args = _build_parser().parse_args(
            ["validate", "-m", "M1", "-t", "T3", "--vc", "168",
             "--fz", "0.12", "--ap", "8", "--ae", "4", "--no-converge"]
        )
        self.assertTrue(args.no_converge)


class TestCliExecution(ZrCutTestCase):
    """CLI 命令执行与输出格式。"""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_meta_outputs_valid_json(self) -> None:
        code, out, _ = self._run(["meta"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("materials", data)
        self.assertEqual(len(data["materials"]), 5)

    def test_recommend_outputs_json(self) -> None:
        code, out, _ = self._run(
            ["recommend", "-m", "M1", "-t", "T3", "-o", "OP2", "--ra", "1.6"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("primary", data)

    def test_recommend_with_volume(self) -> None:
        code, out, _ = self._run(
            ["recommend", "-m", "M1", "-t", "T3", "-o", "OP2", "--volume", "288000"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsNotNone(data["primary"]["machiningTime"])

    def test_validate_outputs_convergence(self) -> None:
        code, out, _ = self._run(
            ["validate", "-m", "M1", "-t", "T4", "--vc", "180", "--fz", "0.12",
             "--ap", "20", "--ae", "14"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["converged"])

    def test_predict_outputs_intervals(self) -> None:
        code, out, _ = self._run(
            ["predict", "-m", "M1", "-t", "T3", "--vc", "168",
             "--fz", "0.12", "--ap", "8"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("ra", data)

    def test_search_outputs_hits(self) -> None:
        code, out, _ = self._run(["search", "304 不锈钢 内冷"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertFalse(data["empty"])

    def test_card_prints_markdown(self) -> None:
        code, out, _ = self._run(
            ["card", "--part", "底板", "--blank", "100x60x20",
             "-m", "M1", "-t", "T3", "-o", "OP2"]
        )
        self.assertEqual(code, 0)
        self.assertIn("工艺卡", out)
        self.assertIn("主轴转速", out)

    def test_card_with_issue(self) -> None:
        code, out, _ = self._run(
            ["card", "--part", "底板", "--blank", "100x60x20",
             "-m", "M1", "-t", "T3", "-o", "OP2", "--issue", "工艺-张"]
        )
        self.assertEqual(code, 0)
        self.assertIn("LOCKED", out)

    def test_business_error_returns_code_1(self) -> None:
        """业务错误必须以退出码 1 结束并在 stderr 给出错误码。"""
        code, _, err = self._run(
            ["recommend", "-m", "M9", "-t", "T3", "-o", "OP2"]
        )
        self.assertEqual(code, 1)
        self.assertIn("E1003", err)

    def test_coolant_error_reported(self) -> None:
        code, _, err = self._run(
            ["recommend", "-m", "M3", "-t", "T1", "-o", "OP2",
             "--ra", "0.8", "--no-coolant"]
        )
        self.assertEqual(code, 1)
        self.assertIn("E7003", err)

    def test_eval_returns_zero_when_passed(self) -> None:
        code, out, _ = self._run(["eval"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["passed"])


class TestEvalSuites(ZrCutTestCase):
    """四类评估集规模与结构。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.svc = ZrCutService()

    def test_recommendation_has_120(self) -> None:
        self.assertEqual(len(eval_recommendation(self.svc)), 120)

    def test_overrun_has_100(self) -> None:
        self.assertEqual(len(eval_overrun(self.svc)), 100)

    def test_prediction_has_100(self) -> None:
        self.assertEqual(len(eval_prediction(self.svc)), 100)

    def test_schema_refusal_has_80(self) -> None:
        self.assertEqual(len(eval_schema_refusal(self.svc)), 80)

    def test_case_ids_unique_within_category(self) -> None:
        for fn in (eval_recommendation, eval_overrun, eval_prediction, eval_schema_refusal):
            results = fn(self.svc)
            ids = [r.case_id for r in results]
            with self.subTest(category=fn.__name__):
                self.assertEqual(len(ids), len(set(ids)))

    def test_overrun_all_detected(self) -> None:
        """越限对抗集必须 100% 检出，这是零漏检的硬要求。"""
        results = eval_overrun(self.svc)
        missed = [r.case_id for r in results if not r.passed]
        self.assertEqual(missed, [], f"漏检用例：{missed}")


class TestComputeMetrics(ZrCutTestCase):
    """六指标计算与一票否决。"""

    def _results(self, n_rec=10, n_ovr=10, n_prd=10, n_sch=10, fail=0):
        out: list[EvalResult] = []
        for i in range(n_rec):
            out.append(EvalResult(f"REC-{i}", "参数合理性", i >= fail))
        for i in range(n_ovr):
            out.append(EvalResult(f"OVR-{i}", "越限对抗", True))
        for i in range(n_prd):
            out.append(
                EvalResult(f"PRD-{i}", "预测精度", True, metrics={"mae": 0.1})
            )
        for i in range(n_sch):
            out.append(EvalResult(f"SCH-{i}", "格式与拒答", True))
        for i in range(n_sch):
            out.append(EvalResult(f"REF-{i}", "格式与拒答", True))
        return out

    def test_all_metrics_present(self) -> None:
        m = compute_metrics(self._results())["metrics"]
        for key in (
            "recommendPassRate", "overrunMissRate", "raMae",
            "schemaValidRate", "refusalAccuracy", "firstClarifyRate",
        ):
            with self.subTest(metric=key):
                self.assertIn(key, m)

    def test_targets_match_constants(self) -> None:
        t = compute_metrics(self._results())["targets"]
        self.assertEqual(t["recommendPassRate"], TARGET_RECOMMEND_PASS)
        self.assertEqual(t["overrunMissRate"], TARGET_OVERRUN_MISS)
        self.assertEqual(t["raMae"], TARGET_RA_MAE)
        self.assertEqual(t["schemaValidRate"], TARGET_SCHEMA_VALID)
        self.assertEqual(t["refusalAccuracy"], TARGET_REFUSAL_ACC)

    def test_counts_total(self) -> None:
        c = compute_metrics(self._results())["counts"]
        self.assertEqual(c["total"], 50)

    def test_passed_when_all_targets_met(self) -> None:
        self.assertTrue(compute_metrics(self._results())["passed"])

    def test_veto_when_overrun_missed(self) -> None:
        """出现越限漏检时一票否决。"""
        results = self._results()
        results[15] = EvalResult("OVR-5", "越限对抗", False)
        report = compute_metrics(results)
        self.assertFalse(report["passed"])
        self.assertTrue(report["vetoFlags"]["overrunMissNonZero"])

    def test_empty_category_not_crash(self) -> None:
        report = compute_metrics([])
        self.assertEqual(report["counts"]["total"], 0)
        self.assertIsInstance(report["passed"], bool)


class TestRunBuiltinEval(ZrCutTestCase):
    """完整内置评估。"""

    def test_runs_and_reports_400_cases(self) -> None:
        report = run_builtin_eval(ZrCutService())
        self.assertEqual(report["report"]["counts"]["total"], 400)

    def test_passes_targets(self) -> None:
        """当前实现必须六指标全达标。"""
        report = run_builtin_eval(ZrCutService())
        failed = {k: v for k, v in report["report"]["verdict"].items() if not v}
        self.assertEqual(failed, {}, f"未达标指标：{failed}")

    def test_zero_failures(self) -> None:
        report = run_builtin_eval(ZrCutService())
        self.assertEqual(report["failureCount"], 0, report["failureSamples"])

    def test_report_serializable(self) -> None:
        report = run_builtin_eval(ZrCutService())
        json.dumps(report, ensure_ascii=False)
