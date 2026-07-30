from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StrategyRegistration:
    name: str
    factory: Callable[..., Any]
    lifecycle: str
    formal_signal_enabled: bool
    ticket_enabled: bool
    description: str


_REGISTRY: dict[str, StrategyRegistration] = {}


def register_strategy(registration: StrategyRegistration) -> StrategyRegistration:
    existing = _REGISTRY.get(registration.name)
    if existing and existing != registration:
        raise ValueError(f"strategy_already_registered:{registration.name}")
    _REGISTRY[registration.name] = registration
    return registration


def get_strategy_registration(name: str) -> StrategyRegistration:
    if name not in _REGISTRY:
        raise KeyError(f"strategy_not_registered:{name}")
    return _REGISTRY[name]


def list_strategies() -> list[StrategyRegistration]:
    return sorted(_REGISTRY.values(), key=lambda item: item.name)


def build_strategy(name: str, *args, **kwargs):
    return get_strategy_registration(name).factory(*args, **kwargs)


def ensure_default_strategies_registered() -> None:
    from overnight_quant.strategy.close_confirmation_v1.strategy import CloseConfirmationStrategy
    from overnight_quant.strategy.legacy_frozen_baseline import LegacyFrozenBaseline

    register_strategy(
        StrategyRegistration(
            name="legacy_frozen_baseline",
            factory=LegacyFrozenBaseline,
            lifecycle="frozen",
            formal_signal_enabled=False,
            ticket_enabled=False,
            description="Frozen reproducibility baseline; demo and research replay only.",
        )
    )
    register_strategy(
        StrategyRegistration(
            name="close_confirmation_v1",
            factory=CloseConfirmationStrategy,
            lifecycle="research_shadow",
            formal_signal_enabled=False,
            ticket_enabled=False,
            description="Industry resonance close-confirmation strategy under shadow validation.",
        )
    )
