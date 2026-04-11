# src/core/engine/__init__.py
"""트레이딩 엔진 패키지 파사드.

기존 `from src.core.engine import X` import 경로의 호환성을 유지하기 위해
모든 public 심볼을 이 모듈에서 re-export한다.
"""
from src.core.engine.registry import (
    _ENGINE_REGISTRY,
    _ENGINE_COLORS,
    register_engine,
)
from src.core.engine.base import MagicSplitEngine

__all__ = [
    "_ENGINE_REGISTRY",
    "_ENGINE_COLORS",
    "register_engine",
    "MagicSplitEngine",
]
