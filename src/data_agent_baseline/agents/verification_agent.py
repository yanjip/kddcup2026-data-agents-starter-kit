"""
验证Agent模块 - 已停用

此模块原先用于反向验证答案的正确性，现已移除所有功能逻辑。
保留文件以兼容现有导入，但所有功能已废弃。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """验证结果 - 已废弃"""
    is_valid: bool = True
    confidence: float = 1.0
    reasoning: str = "Verification disabled"
    suggested_fix: str | None = None
    verified_data_source: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationAgentConfig:
    """验证Agent配置 - 已废弃"""
    max_steps: int = 0
    name: str = "verifier"


class VerificationAgent:
    """
    验证Agent - 已废弃，所有方法返回空值
    """
    def __init__(self, **kwargs) -> None:
        pass

    async def run(self, **kwargs) -> None:
        """验证方法 - 已废弃，始终返回 None"""
        return None


def should_verify_answer(task, answer) -> bool:
    """
    判断是否应该对答案进行验证 - 已废弃，始终返回 False
    """
    return False
