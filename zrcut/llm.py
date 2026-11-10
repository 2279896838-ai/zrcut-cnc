"""LLM 编排层：把确定性工具的结果组织成自然语言回答。

设计要点：
    1. 本层绝不产生数值。所有数值只能来自 tools 返回的结构化结果。
    2. 无 API Key 或网络不可用时自动降级为「模板回答」，保证系统始终可用。
    3. 无论走 LLM 还是模板，最终都要过 Schema 校验与数值一致性检查。
    4. 提示词资产从 prompts/ 目录按编号加载，不在代码里硬编码。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .domain import Confidence
from .errors import ErrorCode, ZrCutError

logger = logging.getLogger(__name__)

_PROMPT_ROOT = Path(__file__).resolve().parent.parent / "prompts"

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def load_prompt(asset_id: str) -> str:
    """从 prompts/ 目录加载提示词资产正文。

    Args:
        asset_id: 资产编号，如 ZRCNC-SYS-001。

    Returns:
        提示词正文（代码块内容）。

    Raises:
        ZrCutError: 资产文件不存在。
    """
    matches = list(_PROMPT_ROOT.glob(f"**/{asset_id}.md"))
    if not matches:
        raise ZrCutError(
            ErrorCode.INTERNAL_ERROR,
            f"提示词资产不存在：{asset_id}",
            {"assetId": asset_id, "root": str(_PROMPT_ROOT)},
            500,
        )
    text = matches[0].read_text(encoding="utf-8")
    m = re.search(r"```text\n(.*?)```", text, re.S)
    return m.group(1).rstrip() if m else text


def render_template(template: str, variables: dict[str, Any]) -> str:
    """把 {{变量名}} 占位符替换为实际值。

    Args:
        template: 含占位符的模板。
        variables: 变量字典；缺失变量替换为「未知」。

    Returns:
        渲染后的文本。
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        val = variables.get(key)
        if val is None:
            return "未知"
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", repl, template)


@dataclass(slots=True)
class LLMConfig:
    """LLM 接入配置。

    Attributes:
        base_url: API 基址。
        model: 模型名。
        api_key: 密钥；为空表示降级模式。
        timeout_s: 超时秒数。
        max_tokens: 最大输出词元。
        temperature: 温度。
    """

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""
    timeout_s: float = 30.0
    max_tokens: int = 1200
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """从环境变量构造；未配置 Key 时返回降级配置。"""
        return cls(
            base_url=os.environ.get("ZRCUT_LLM_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("ZRCUT_LLM_MODEL", "deepseek-chat"),
            api_key=os.environ.get("ZRCUT_LLM_API_KEY", "").strip(),
            timeout_s=float(os.environ.get("ZRCUT_LLM_TIMEOUT", "30")),
        )

    @property
    def enabled(self) -> bool:
        """是否具备真实调用条件。"""
        return bool(self.api_key)


@dataclass(slots=True)
class LLMResult:
    """LLM 调用结果。

    Attributes:
        text: 生成的文本。
        mode: 实际使用的模式，llm 或 template。
        degraded_reason: 降级原因。
        usage: token 用量。
    """

    text: str
    mode: str = "template"
    degraded_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "mode": self.mode,
            "degradedReason": self.degraded_reason,
            "usage": self.usage,
        }


class LLMClient:
    """极简 LLM 客户端，只用标准库 urllib。

    Attributes:
        config: 配置。
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()

    def complete(self, system: str, user: str) -> LLMResult:
        """执行一次补全。

        Args:
            system: 系统提示词。
            user: 用户消息。

        Returns:
            LLMResult；调用失败时返回降级结果而非抛异常。
        """
        if not self.config.enabled:
            return LLMResult(
                text="",
                mode="template",
                degraded_reason="未配置 ZRCUT_LLM_API_KEY，使用模板回答",
            )

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return LLMResult(
                text=text,
                mode="llm",
                usage={
                    "promptTokens": int(usage.get("prompt_tokens", 0)),
                    "completionTokens": int(usage.get("completion_tokens", 0)),
                },
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("LLM 网络调用失败，降级为模板回答: %s", exc)
            return LLMResult(
                text="", mode="template", degraded_reason=f"网络不可用：{exc}"
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("LLM 响应解析失败，降级为模板回答: %s", exc)
            return LLMResult(
                text="", mode="template", degraded_reason=f"响应格式异常：{exc}"
            )


def check_numeric_discipline(
    text: str, allowed_values: Sequence[float], *, tolerance: float = 0.02
) -> list[str]:
    """检查输出文本中的数值是否全部来自工具返回值。

    这是规范 3.3 节「数值纪律」的机器化执行：文本中出现的每个数字，
    必须能在允许值集合中找到同值（容差 ±2%），否则判为模型自算。

    Args:
        text: 待检查文本。
        allowed_values: 允许的数值集合（来自工具返回）。
        tolerance: 相对容差。

    Returns:
        违规数值的描述列表；空列表表示通过。
    """
    allowed = sorted(float(v) for v in allowed_values if v is not None)
    violations: list[str] = []
    for raw in NUM_RE.findall(text):
        try:
            val = float(raw)
        except ValueError:
            continue
        # 小整数通常是序号、档位、公式系数，不参与判定
        if abs(val) < 1e-9:
            continue
        hit = any(
            abs(val - a) <= max(abs(a) * tolerance, 1e-9) for a in allowed
        )
        if not hit and val not in (0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 60.0, 100.0):
            violations.append(raw)
    return violations


def build_user_message(payload: dict[str, Any]) -> str:
    """把结构化工具结果序列化为发给 LLM 的用户消息。

    Args:
        payload: 工具结果字典。

    Returns:
        用户消息文本。
    """
    parts = ["以下是系统工具返回的结构化结果，请严格据此组织回答，不得增删数值："]
    parts.append("```json")
    parts.append(json.dumps(payload, ensure_ascii=False, indent=2))
    parts.append("```")
    return "\n".join(parts)
