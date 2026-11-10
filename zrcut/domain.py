"""智刃工艺（ZR-CUT）核心领域常量。

本模块是全系统唯一的物理与工艺事实来源。任何数值计算都只能引用这里的常量，
不得在业务代码中硬编码材料性能、刀具尺寸、机床极限。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MaterialId(str, Enum):
    """材料代号，对应规范 3.2 节。"""

    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"


class ToolId(str, Enum):
    """刀具代号，对应规范 3.3 节。"""

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"
    T6 = "T6"


class OperationId(str, Enum):
    """加工方式代号，对应规范 3.4 节。"""

    OP1 = "OP1"
    OP2 = "OP2"
    OP3 = "OP3"
    OP4 = "OP4"


class Strategy(str, Enum):
    """参数策略，对应规范 4.2 节。"""

    EFFICIENCY = "EFFICIENCY"
    QUALITY = "QUALITY"
    LIFE = "LIFE"
    BALANCED = "BALANCED"


class ChatterRisk(str, Enum):
    """颤振风险等级，对应规范 3.8 节。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Confidence(str, Enum):
    """结论置信度，对应规范 4.2 节。"""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RefusalReason(str, Enum):
    """拒答枚举，对应规范 4.3 节。"""

    MACHINE_LIMIT = "MACHINE_LIMIT"
    NO_EVIDENCE = "NO_EVIDENCE"
    SKIP_TRIAL_CUT = "SKIP_TRIAL_CUT"
    UNSAFE_SETUP = "UNSAFE_SETUP"
    ROLE_RESTRICTED = "ROLE_RESTRICTED"
    CARD_LOCKED = "CARD_LOCKED"


@dataclass(frozen=True, slots=True)
class Material:
    """材料属性。

    Attributes:
        code: 材料代号。
        name: 材料中文名。
        grade: 牌号与状态。
        hardness: 硬度描述。
        kc: 比切削力，N/mm²。
        taylor_c: Taylor 公式常数 C。
        taylor_n: Taylor 公式指数 n。
        k_ra: 表面粗糙度修正系数。
        vc_min: 切削速度推荐下限，m/min。
        vc_max: 切削速度推荐上限，m/min。
        fz_min: 每齿进给推荐下限，mm/z。
        fz_max: 每齿进给推荐上限，mm/z。
        ae_d_max: 径向切宽与直径比上限。
        ap_d_max: 轴向切深与直径比上限。
        requires_coolant: 是否强制内冷或高压外冷。
    """

    code: MaterialId
    name: str
    grade: str
    hardness: str
    kc: float
    taylor_c: float
    taylor_n: float
    k_ra: float
    vc_min: float
    vc_max: float
    fz_min: float
    fz_max: float
    ae_d_max: float
    ap_d_max: float
    requires_coolant: bool = False


MATERIALS: dict[MaterialId, Material] = {
    MaterialId.M1: Material(
        code=MaterialId.M1,
        name="中碳钢",
        grade="45，正火",
        hardness="HB 180-220",
        kc=2000.0,
        taylor_c=350.0,
        taylor_n=0.25,
        k_ra=1.6,
        vc_min=120.0,
        vc_max=180.0,
        fz_min=0.05,
        fz_max=0.12,
        ae_d_max=0.5,
        ap_d_max=1.0,
    ),
    MaterialId.M2: Material(
        code=MaterialId.M2,
        name="铝合金",
        grade="6061-T6",
        hardness="HB 95",
        kc=700.0,
        taylor_c=1800.0,
        taylor_n=0.40,
        k_ra=1.2,
        vc_min=300.0,
        vc_max=500.0,
        fz_min=0.08,
        fz_max=0.20,
        ae_d_max=0.7,
        ap_d_max=2.0,
    ),
    MaterialId.M3: Material(
        code=MaterialId.M3,
        name="不锈钢",
        grade="304",
        hardness="HB 185",
        kc=2600.0,
        taylor_c=160.0,
        taylor_n=0.20,
        k_ra=2.2,
        vc_min=60.0,
        vc_max=100.0,
        fz_min=0.04,
        fz_max=0.08,
        ae_d_max=0.4,
        ap_d_max=0.5,
        requires_coolant=True,
    ),
    MaterialId.M4: Material(
        code=MaterialId.M4,
        name="球墨铸铁",
        grade="QT600-3",
        hardness="HB 220-270",
        kc=1500.0,
        taylor_c=280.0,
        taylor_n=0.25,
        k_ra=2.0,
        vc_min=100.0,
        vc_max=150.0,
        fz_min=0.06,
        fz_max=0.12,
        ae_d_max=0.5,
        ap_d_max=0.8,
    ),
    MaterialId.M5: Material(
        code=MaterialId.M5,
        name="钛合金",
        grade="TC4",
        hardness="HB 320",
        kc=2100.0,
        taylor_c=80.0,
        taylor_n=0.18,
        k_ra=2.5,
        vc_min=30.0,
        vc_max=60.0,
        fz_min=0.03,
        fz_max=0.06,
        ae_d_max=0.3,
        ap_d_max=0.3,
        requires_coolant=True,
    ),
}


@dataclass(frozen=True, slots=True)
class Tool:
    """刀具属性。

    Attributes:
        code: 刀具代号。
        kind: 刀具类型。
        diameter: 直径 D，mm。
        teeth: 齿数 z。
        coating: 材质与涂层。
        overhang: 悬伸长度 L，mm。
        flute_length: 刃长，mm。
        corner_radius: 刀尖圆弧半径 rε，mm；球头刀为球半径 R。
        is_ball: 是否球头刀。
    """

    code: ToolId
    kind: str
    diameter: float
    teeth: int
    coating: str
    overhang: float
    flute_length: float
    corner_radius: float
    is_ball: bool = False

    @property
    def overhang_ratio(self) -> float:
        """悬伸比 L/D。"""
        return self.overhang / self.diameter


TOOLS: dict[ToolId, Tool] = {
    ToolId.T1: Tool(ToolId.T1, "立铣刀", 10.0, 4, "硬质合金 TiAlN", 40.0, 22.0, 0.8),
    ToolId.T2: Tool(ToolId.T2, "立铣刀", 12.0, 4, "硬质合金 TiAlN", 45.0, 26.0, 0.8),
    ToolId.T3: Tool(ToolId.T3, "立铣刀", 16.0, 4, "硬质合金 TiAlN", 55.0, 32.0, 0.8),
    ToolId.T4: Tool(ToolId.T4, "立铣刀", 20.0, 4, "硬质合金 AlCrN", 65.0, 40.0, 0.8),
    ToolId.T5: Tool(ToolId.T5, "球头刀", 10.0, 2, "硬质合金 TiAlN", 50.0, 25.0, 5.0, is_ball=True),
    ToolId.T6: Tool(ToolId.T6, "面铣刀", 80.0, 5, "硬质合金 可转位", 80.0, 0.0, 1.2),
}


@dataclass(frozen=True, slots=True)
class Operation:
    """加工方式属性。"""

    code: OperationId
    name: str
    typical_tools: tuple[ToolId, ...]


OPERATIONS: dict[OperationId, Operation] = {
    OperationId.OP1: Operation(OperationId.OP1, "端面铣", (ToolId.T6,)),
    OperationId.OP2: Operation(OperationId.OP2, "侧铣（轮廓）", (ToolId.T1, ToolId.T2, ToolId.T3, ToolId.T4)),
    OperationId.OP3: Operation(OperationId.OP3, "型腔粗加工", (ToolId.T1, ToolId.T2, ToolId.T3, ToolId.T4)),
    OperationId.OP4: Operation(OperationId.OP4, "曲面精加工", (ToolId.T5,)),
}


@dataclass(frozen=True, slots=True)
class Machine:
    """机床属性，对应锚点机床 VMC-850。"""

    code: str
    name: str
    control_system: str
    spindle_max_rpm: float
    power_continuous_kw: float
    power_peak_kw: float
    torque_nominal_nm: float
    taper: str
    travel_xyz_mm: tuple[float, float, float]
    table_mm: tuple[float, float]
    table_load_kg: float
    rapid_xy_mmpm: float
    rapid_z_mmpm: float
    feed_min_mmpm: float
    feed_max_mmpm: float
    positioning_accuracy_mm: float
    repeatability_mm: float
    coolant_type: str
    coolant_pressure_mpa: tuple[float, float]
    natural_frequency_hz: float


MACHINE = Machine(
    code="EQ-A01-0037",
    name="VMC-850 立式加工中心",
    control_system="FANUC 0i-MF",
    spindle_max_rpm=8000.0,
    power_continuous_kw=11.0,
    power_peak_kw=15.0,
    torque_nominal_nm=70.0,
    taper="BT40",
    travel_xyz_mm=(850.0, 500.0, 550.0),
    table_mm=(1000.0, 500.0),
    table_load_kg=600.0,
    rapid_xy_mmpm=24000.0,
    rapid_z_mmpm=18000.0,
    feed_min_mmpm=1.0,
    feed_max_mmpm=10000.0,
    positioning_accuracy_mm=0.008,
    repeatability_mm=0.005,
    coolant_type="乳化液外冷",
    coolant_pressure_mpa=(0.3, 0.5),
    natural_frequency_hz=320.0,
)

# 颤振判定使用的叶瓣序数
CHATTER_LOBES: tuple[int, ...] = (1, 2, 3)

# 稳定带相对宽度（±3%）
CHATTER_BAND_TOLERANCE = 0.03

# 悬伸比刚性阈值
LD_SAFE_MAX = 4.0
LD_WARN_MAX = 6.0

# 薄壁件判定阈值，mm
THIN_WALL_MM = 3.0

# 薄壁件切深折减系数
THIN_WALL_AP_FACTOR = 0.5

# 超长悬伸切深折减系数（LD 在 4-6 区间）
LONG_OVERHANG_AP_FACTOR = 0.7

# 默认质量要求，μm
DEFAULT_RA_REQ = 3.2

# 预测模型偏离理论值告警阈值
DEVIATION_WARN_RATIO = 0.40

# 回填偏差阈值
CLOSURE_RA_TOLERANCE = 0.25
CLOSURE_TIME_TOLERANCE = 0.30
CLOSURE_LIFE_TOLERANCE = 0.40


def get_material(code: str | MaterialId) -> Material:
    """按代号取材料，未登记则抛 KeyError。"""
    return MATERIALS[MaterialId(code)]


def get_tool(code: str | ToolId) -> Tool:
    """按代号取刀具，未登记则抛 KeyError。"""
    return TOOLS[ToolId(code)]


def get_operation(code: str | OperationId) -> Operation:
    """按代号取加工方式，未登记则抛 KeyError。"""
    return OPERATIONS[OperationId(code)]


def chatter_stable_rpm(teeth: int, lobe: int) -> float:
    """计算第 lobe 阶叶瓣的稳定转速中心值。

    Args:
        teeth: 刀具齿数 z。
        lobe: 叶瓣序数 k，取正整数。

    Returns:
        稳定转速，r/min。

    Raises:
        ValueError: 齿数或叶瓣序数非正。
    """
    if teeth <= 0:
        raise ValueError(f"齿数必须为正整数，当前 {teeth}")
    if lobe <= 0:
        raise ValueError(f"叶瓣序数必须为正整数，当前 {lobe}")
    return 60.0 * MACHINE.natural_frequency_hz / (lobe * teeth)
