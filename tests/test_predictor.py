"""小样本回归预测器测试。

覆盖岭回归拟合的正确性、正则强度选择、置信区间、
无样本退化行为，以及比值建模对模型输出的物理约束。
"""

from __future__ import annotations

import math

import numpy as np

from tests import ZrCutTestCase
from zrcut.domain import Confidence, get_material, get_tool
from zrcut.predictor import (
    FEATURE_NAMES,
    Predictor,
    PredictorError,
    deviation_ratio,
    fit_ridge,
)


def _synthetic(n: int = 12, noise: float = 0.0, seed: int = 42):
    """构造可控的合成训练数据。

    目标量是特征的线性组合加常数，便于验证回归能否复原真实关系。

    Args:
        n: 样本数。
        noise: 噪声幅度。
        seed: 随机种子。

    Returns:
        (特征矩阵, 目标向量)。
    """
    rng = np.random.default_rng(seed)
    x = rng.uniform(0.5, 2.0, size=(n, len(FEATURE_NAMES)))
    # 真实关系：0.3*x0 + 0.5*x4 + 常数
    y = 0.3 * x[:, 0] + 0.5 * x[:, 4] + 0.2
    if noise > 0:
        y = y + rng.normal(0, noise, size=n)
    return x, y


class TestFitRidge(ZrCutTestCase):
    """岭回归拟合。"""

    def test_recovers_linear_relation(self) -> None:
        """无噪声时应能近乎精确复原线性关系。"""
        x, y = _synthetic(n=20, noise=0.0)
        model = fit_ridge(x, y)
        pred = model.predict(x)
        for got, want in zip(pred, y):
            self.assertAlmostEqualRel(got, want, rel=0.05)

    def test_noise_increases_residual_std(self) -> None:
        """噪声越大，残差标准差应越大。"""
        x1, y1 = _synthetic(n=30, noise=0.01, seed=1)
        x2, y2 = _synthetic(n=30, noise=0.20, seed=1)
        m1 = fit_ridge(x1, y1)
        m2 = fit_ridge(x2, y2)
        self.assertLess(m1.residual_std, m2.residual_std)

    def test_selects_from_grid(self) -> None:
        """选中的正则强度必须来自候选网格。"""
        grid = (1e-3, 1e-2, 1e-1, 1.0)
        x, y = _synthetic(n=15)
        model = fit_ridge(x, y, l2_grid=grid)
        self.assertIn(model.l2, grid)

    def test_rejects_too_few_samples(self) -> None:
        """样本数少于 2 必须拒绝。"""
        with self.assertRaises(PredictorError):
            fit_ridge([[1.0] * 8], [1.0])

    def test_rejects_wrong_feature_count(self) -> None:
        """特征维度不匹配必须拒绝。"""
        with self.assertRaises(PredictorError):
            fit_ridge([[1.0, 2.0], [3.0, 4.0]], [1.0, 2.0])

    def test_rejects_length_mismatch(self) -> None:
        """x 与 y 样本数不一致必须拒绝。"""
        x, _ = _synthetic(n=5)
        with self.assertRaises(PredictorError):
            fit_ridge(x, [1.0, 2.0])

    def test_handles_constant_feature(self) -> None:
        """常数列（标准差为 0）不应导致除零崩溃。"""
        x, y = _synthetic(n=8)
        x[:, 3] = 4.0
        model = fit_ridge(x, y)
        self.assertTrue(np.isfinite(model.predict(x)).all())

    def test_predict_single_row(self) -> None:
        """单行输入应返回长度为 1 的数组。"""
        x, y = _synthetic(n=10)
        model = fit_ridge(x, y)
        out = model.predict(x[0])
        self.assertEqual(out.shape, (1,))


class TestPredictorDegradation(ZrCutTestCase):
    """无样本时的退化行为。"""

    def test_no_model_returns_theory_with_low_confidence(self) -> None:
        """无训练样本时必须返回公式值并标注 LOW。"""
        p = Predictor()
        out = p.predict(
            [1.0] * 8, ra_theory=0.5, life_theory=20.0, time_theory=1.5
        )
        self.assertAlmostEqualRel(out["ra"].value, 0.5, 1e-9)
        self.assertIs(out["ra"].confidence, Confidence.LOW)
        self.assertEqual(out["ra"].n_samples, 0)
        self.assertIn("退化", out["ra"].basis)

    def test_interval_brackets_theory_value(self) -> None:
        """区间必须包含点预测值。"""
        p = Predictor()
        out = p.predict([1.0] * 8, ra_theory=0.5, life_theory=20.0, time_theory=1.5)
        for key in ("ra", "life", "time"):
            iv = out[key]
            with self.subTest(metric=key):
                self.assertLessEqual(iv.low, iv.value)
                self.assertGreaterEqual(iv.high, iv.value)

    def test_rejects_wrong_feature_length(self) -> None:
        p = Predictor()
        with self.assertRaises(PredictorError):
            p.predict([1.0, 2.0], ra_theory=0.5, life_theory=20.0, time_theory=1.5)


class TestPredictorWithSamples(ZrCutTestCase):
    """有训练样本时的行为。"""

    def _train(self, n: int = 12, noise: float = 0.02):
        """训练一个完整预测器。"""
        rng = np.random.default_rng(7)
        x = rng.uniform(0.8, 1.5, size=(n, len(FEATURE_NAMES)))
        ratios_ra = rng.uniform(0.9, 1.1, size=n) + np.zeros(n)
        ratios_life = rng.uniform(0.85, 1.15, size=n)
        ratios_time = rng.uniform(0.95, 1.05, size=n)
        p = Predictor()
        p.fit(
            x,
            ra_actual=list(ratios_ra * 1.0),
            ra_theory=[1.0] * n,
            life_actual=list(ratios_life * 20.0),
            life_theory=[20.0] * n,
            time_actual=list(ratios_time * 2.0),
            time_theory=[2.0] * n,
        )
        return p, x

    def test_models_are_fitted(self) -> None:
        """三个目标模型都应完成拟合。"""
        p, _ = self._train()
        self.assertIsNotNone(p.ra_model)
        self.assertIsNotNone(p.life_model)
        self.assertIsNotNone(p.time_model)

    def test_confidence_not_low_with_enough_samples(self) -> None:
        """样本数达标时置信度应高于 LOW。"""
        p, x = self._train(n=14)
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        self.assertIsNot(out["ra"].confidence, Confidence.LOW)

    def test_medium_confidence_with_few_samples(self) -> None:
        """样本数在 MEDIUM 区间时置信度应为 MEDIUM 或更高。"""
        p, x = self._train(n=5)
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        self.assertIn(
            out["ra"].confidence, (Confidence.MEDIUM, Confidence.HIGH)
        )

    def test_prediction_stays_near_theory(self) -> None:
        """比值建模必须把预测值约束在公式值附近，不得数量级偏离。"""
        p, x = self._train()
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        self.assertLess(out["ra"].value, 1.0 * 5)
        self.assertGreater(out["ra"].value, 1.0 * 0.2)

    def test_interval_is_positive(self) -> None:
        """区间上下界必须为正，避免出现负的时间或寿命。"""
        p, x = self._train()
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        for key in ("ra", "life", "time"):
            with self.subTest(metric=key):
                self.assertGreater(out[key].low, 0.0)
                self.assertGreater(out[key].high, out[key].low)

    def test_relative_width_non_negative(self) -> None:
        """区间相对宽度不得为负。"""
        p, x = self._train()
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        for key in ("ra", "life", "time"):
            self.assertGreaterEqual(out[key].relative_width, 0.0)

    def test_to_dict_serializable(self) -> None:
        """区间对象必须能安全序列化。"""
        p, x = self._train()
        out = p.predict(
            list(x[0]), ra_theory=1.0, life_theory=20.0, time_theory=2.0
        )
        d = out["ra"].to_dict()
        self.assertIn("value", d)
        self.assertIn("confidence", d)
        self.assertIn("nSamples", d)


class TestRatioTargetValidation(ZrCutTestCase):
    """比值目标的输入校验。"""

    def test_mismatched_lengths_raise(self) -> None:
        p = Predictor()
        x = [[1.0] * 8 for _ in range(3)]
        with self.assertRaises(PredictorError):
            p.fit(x, ra_actual=[1.0, 2.0], ra_theory=[1.0, 1.0, 1.0])

    def test_zero_theory_raises(self) -> None:
        p = Predictor()
        x = [[1.0] * 8 for _ in range(2)]
        with self.assertRaises(PredictorError):
            p.fit(x, ra_actual=[1.0, 1.0], ra_theory=[0.0, 1.0])


class TestDeviationRatio(ZrCutTestCase):
    """偏离度计算。"""

    def test_positive_when_model_higher(self) -> None:
        self.assertAlmostEqualRel(deviation_ratio(1.2, 1.0), 0.2, 1e-9)

    def test_negative_when_model_lower(self) -> None:
        self.assertAlmostEqualRel(deviation_ratio(0.8, 1.0), -0.2, 1e-9)

    def test_zero_theory_raises(self) -> None:
        with self.assertRaises(PredictorError):
            deviation_ratio(1.0, 0.0)


class TestFeatureSchema(ZrCutTestCase):
    """特征定义稳定性。"""

    def test_feature_count_is_eight(self) -> None:
        """规范要求 8 维特征，不得随意变更。"""
        self.assertEqual(len(FEATURE_NAMES), 8)

    def test_feature_names_expected(self) -> None:
        """特征顺序是模型契约的一部分。"""
        self.assertEqual(
            FEATURE_NAMES,
            ("hardness", "kc", "diameter", "teeth", "corner_radius", "vc", "fz", "ap"),
        )
