"""小样本回归预测器。

工业场景下同材料同刀具的试切样本通常只有十几到几十条，深度模型不可用。
本模块用岭回归（闭式解）配合经验贝叶斯式的区间估计，给出：
    - 点预测值
    - 90% 置信区间
    - 可信度（受样本量驱动）

设计要点：
    1. 特征标准化后求解，避免量纲差异导致病态。
    2. 正则强度由广义交叉验证（GCV）在候选网格上选取，无需外部库。
    3. 区间半宽由留一交叉验证（LOOCV）残差标准差 × 1.645 得到。
    4. 样本不足时退化为物理公式值，并标注 LOW 置信度。

与 calc 模块的关系：predict 只做「修正」，基准值永远来自确定性公式，
模型不允许凭空产生数量级不同的结果。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .domain import Confidence


class PredictorError(ValueError):
    """预测器输入不合法时抛出。"""


@dataclass(frozen=True, slots=True)
class PredictionInterval:
    """带区间的预测结果。

    Attributes:
        value: 点预测值。
        low: 90% 区间下界。
        high: 90% 区间上界。
        relative_width: 区间相对宽度 (high-low)/value。
        confidence: 可信度等级。
        basis: 预测依据说明。
        n_samples: 参与拟合的样本数。
    """

    value: float
    low: float
    high: float
    relative_width: float
    confidence: Confidence
    basis: str
    n_samples: int

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "value": round(self.value, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "relativeWidth": round(self.relative_width, 4),
            "confidence": self.confidence.value,
            "basis": self.basis,
            "nSamples": self.n_samples,
        }


FEATURE_NAMES: tuple[str, ...] = (
    "hardness",
    "kc",
    "diameter",
    "teeth",
    "corner_radius",
    "vc",
    "fz",
    "ap",
)


@dataclass(slots=True)
class RidgeModel:
    """标准化 + 岭回归模型。

    Attributes:
        coef: 标准化空间下的回归系数（不含截距）。
        intercept: 截距。
        mean: 特征均值。
        std: 特征标准差。
        l2: 正则强度。
        residual_std: 留一交叉验证残差标准差。
        n_samples: 训练样本数。
    """

    coef: np.ndarray
    intercept: float
    mean: np.ndarray
    std: np.ndarray
    l2: float
    residual_std: float
    n_samples: int

    def predict(self, x: Sequence[Sequence[float]]) -> np.ndarray:
        """对标准化后的输入做预测。

        Args:
            x: 形状 (m, 8) 的原始特征矩阵。

        Returns:
            形状 (m,) 的预测值。
        """
        arr = np.asarray(x, dtype=float)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        z = (arr - self.mean) / self.std
        return z @ self.coef + self.intercept


def _standardize(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """计算每列均值与标准差，标准差为 0 时置 1 避免除零。"""
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std < 1e-12, 1.0, std)
    return mean, std


def fit_ridge(
    x: Sequence[Sequence[float]],
    y: Sequence[float],
    *,
    l2_grid: Sequence[float] = (1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0),
) -> RidgeModel:
    """拟合岭回归，正则强度由 GCV 选定。

    Args:
        x: 形状 (n, 8) 特征矩阵，n ≥ 2。
        y: 形状 (n,) 目标向量。
        l2_grid: 候选正则强度。

    Returns:
        RidgeModel 实例。

    Raises:
        PredictorError: 样本数不足或形状不匹配。
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    if xa.ndim != 2:
        raise PredictorError(f"特征矩阵须为二维，当前维度 {xa.ndim}")
    if ya.ndim != 1:
        raise PredictorError(f"目标向量须为一维，当前维度 {ya.ndim}")
    n, p = xa.shape
    if n != ya.shape[0]:
        raise PredictorError(f"样本数不一致：x {n} 行，y {ya.shape[0]} 个")
    if n < 2:
        raise PredictorError(f"样本数至少 2 条，当前 {n}")
    if p != len(FEATURE_NAMES):
        raise PredictorError(f"特征维度须为 {len(FEATURE_NAMES)}，当前 {p}")

    mean, std = _standardize(xa)
    z = (xa - mean) / std

    best = None
    for l2 in l2_grid:
        model = _solve(z, ya, mean, std, l2)
        # GCV：使用帽子矩阵迹的闭式近似
        s = np.linalg.svd(z, compute_uv=False)
        denom = s * s
        trace_h = float(np.sum(denom / (denom + l2))) + 1.0  # +1 为截距
        resid = ya - model.predict(xa)
        rss = float(resid @ resid)
        gcv = n * rss / max((n - trace_h) ** 2, 1e-12)
        score = gcv
        if best is None or score < best[0]:
            best = (score, model)

    assert best is not None
    chosen = best[1]

    # LOOCV 残差标准差
    resid = ya - chosen.predict(xa)
    dof = max(n - min(p, n - 1) - 1, 1)
    # 用留一残差近似：e_i / (1 - h_ii)
    s = np.linalg.svd(z, compute_uv=False)
    denom = s * s
    h_trace = float(np.sum(denom / (denom + chosen.l2)))
    h_mean = (h_trace + 1.0) / n
    loo_resid = resid / max(1.0 - min(h_mean, 0.95), 0.05)
    sigma = float(np.sqrt(np.sum(loo_resid ** 2) / max(dof, 1)))
    chosen.residual_std = sigma
    chosen.n_samples = n
    return chosen


def _solve(
    z: np.ndarray, y: np.ndarray, mean: np.ndarray, std: np.ndarray, l2: float
) -> RidgeModel:
    """在标准化空间求解带截距的岭回归闭式解。"""
    n, p = z.shape
    z_mean = z.mean(axis=0)
    y_mean = float(y.mean())
    zc = z - z_mean
    yc = y - y_mean
    a = zc.T @ zc + l2 * np.eye(p)
    b = zc.T @ yc
    try:
        coef = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        coef = np.linalg.lstsq(a, b, rcond=None)[0]
    # 还原到原始标准化空间（mean/std 已是原始尺度）
    intercept = y_mean - float(z_mean @ coef)
    return RidgeModel(
        coef=coef,
        intercept=intercept,
        mean=mean,
        std=std,
        l2=l2,
        residual_std=0.0,
        n_samples=n,
    )


@dataclass(slots=True)
class Predictor:
    """小样本预测器，管理三个目标量的模型。

    Attributes:
        ra_model: 粗糙度修正模型，预测 Ra 实测/理论比值。
        life_model: 寿命修正模型，预测 T 实测/Taylor 比值。
        time_model: 工时修正模型，预测 Tm 实测/理论比值。
        min_samples_for_high: 判定 HIGH 置信度所需最小样本数。
        min_samples_for_medium: 判定 MEDIUM 置信度所需最小样本数。
    """

    ra_model: RidgeModel | None = None
    life_model: RidgeModel | None = None
    time_model: RidgeModel | None = None
    min_samples_for_high: int = 10
    min_samples_for_medium: int = 3

    def fit(
        self,
        x: Sequence[Sequence[float]],
        ra_actual: Sequence[float] | None = None,
        life_actual: Sequence[float] | None = None,
        time_actual: Sequence[float] | None = None,
        *,
        ra_theory: Sequence[float] | None = None,
        life_theory: Sequence[float] | None = None,
        time_theory: Sequence[float] | None = None,
    ) -> "Predictor":
        """拟合三个目标模型。

        模型学习的是「实测/理论」的比值，而不是绝对值。这样模型只能在物理
        公式附近做修正，不会出现数量级偏离。

        Args:
            x: 形状 (n, 8) 特征矩阵。
            ra_actual: 实测粗糙度序列。
            life_actual: 实测寿命序列。
            time_actual: 实测工时序列。
            ra_theory: 理论粗糙度序列，与 ra_actual 配对。
            life_theory: Taylor 寿命序列。
            time_theory: 理论工时序列。

        Returns:
            self，便于链式调用。

        Raises:
            PredictorError: 目标序列与理论序列缺失或长度不符。
        """
        if ra_actual is not None and ra_theory is not None:
            y = _ratio_target(ra_actual, ra_theory, "Ra")
            if len(y) >= 2:
                self.ra_model = fit_ridge(x[: len(y)], y)
        if life_actual is not None and life_theory is not None:
            y = _ratio_target(life_actual, life_theory, "T")
            if len(y) >= 2:
                self.life_model = fit_ridge(x[: len(y)], y)
        if time_actual is not None and time_theory is not None:
            y = _ratio_target(time_actual, time_theory, "Tm")
            if len(y) >= 2:
                self.time_model = fit_ridge(x[: len(y)], y)
        return self

    def _interval(
        self,
        model: RidgeModel | None,
        x: Sequence[float],
        theory: float,
        label: str,
    ) -> PredictionInterval:
        """基于比值模型构造带区间的预测。"""
        if model is None:
            return PredictionInterval(
                value=theory,
                low=theory * 0.8,
                high=theory * 1.2,
                relative_width=0.4,
                confidence=Confidence.LOW,
                basis=f"{label}：无同材料同刀具历史样本，退化为公式值",
                n_samples=0,
            )
        ratio = float(model.predict([x])[0])
        ratio = max(ratio, 0.1)
        value = theory * ratio
        half = 1.645 * model.residual_std
        lo_r = max(ratio - half, 0.1)
        hi_r = ratio + half
        low = theory * lo_r
        high = theory * hi_r
        width = (high - low) / value if value else 0.0
        if model.n_samples >= self.min_samples_for_high and width <= 0.5:
            conf = Confidence.HIGH
        elif model.n_samples >= self.min_samples_for_medium:
            conf = Confidence.MEDIUM
        else:
            conf = Confidence.LOW
        basis = (
            f"{label}：公式值 {theory:.4g} × 模型修正 {ratio:.3f}"
            f"（样本 {model.n_samples} 条，λ={model.l2:g}）"
        )
        return PredictionInterval(
            value=value,
            low=low,
            high=high,
            relative_width=width,
            confidence=conf,
            basis=basis,
            n_samples=model.n_samples,
        )

    def predict(
        self,
        features: Sequence[float],
        *,
        ra_theory: float,
        life_theory: float,
        time_theory: float,
    ) -> dict[str, PredictionInterval]:
        """对单个工况给出三项预测及区间。

        Args:
            features: 长度 8 的特征向量。
            ra_theory: 理论粗糙度，μm。
            life_theory: Taylor 寿命，min。
            time_theory: 理论工时，min。

        Returns:
            键为 ra / life / time 的字典。
        """
        if len(features) != len(FEATURE_NAMES):
            raise PredictorError(f"特征向量长度须为 {len(FEATURE_NAMES)}，当前 {len(features)}")
        return {
            "ra": self._interval(self.ra_model, features, ra_theory, "表面粗糙度 Ra"),
            "life": self._interval(self.life_model, features, life_theory, "刀具寿命 T"),
            "time": self._interval(self.time_model, features, time_theory, "加工时间 Tm"),
        }


def _ratio_target(
    actual: Sequence[float], theory: Sequence[float], label: str
) -> list[float]:
    """计算实测与理论的比值序列。"""
    if len(actual) != len(theory):
        raise PredictorError(
            f"{label} 实测与理论长度不一致：{len(actual)} vs {len(theory)}"
        )
    out: list[float] = []
    for a, t in zip(actual, theory):
        if t <= 0:
            raise PredictorError(f"{label} 理论值须为正，当前 {t}")
        out.append(float(a) / float(t))
    return out


def deviation_ratio(model_value: float, theory_value: float) -> float:
    """计算模型值与理论值的相对偏离。

    Args:
        model_value: 模型预测值。
        theory_value: 理论公式值。

    Returns:
        相对偏离，正值表示模型偏高。
    """
    if theory_value <= 0:
        raise PredictorError(f"理论值须为正，当前 {theory_value}")
    return (model_value - theory_value) / theory_value


TWIN_QUANTILE = 1.645
"""90% 双侧区间对应的标准正态分位数。"""
