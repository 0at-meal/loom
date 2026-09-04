"""Router core package for dynamic payment routing."""

from router_core.bandit import BanditStateRegistry, calculate_gamma_from_half_life
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import (
    PIDConfig,
    PIDDiagnostics,
    PIDState,
    PIDStepResult,
    calculate_pid_step,
    project_to_bounded_simplex,
)
from router_core.router import BanditRouter
from router_core.state import (
    AcquirerState,
    AcquirerStateConfig,
    AcquirerStateSnapshot,
)

__all__ = [
    "AcquirerRouteConfig",
    "AcquirerState",
    "AcquirerStateConfig",
    "AcquirerStateSnapshot",
    "BanditRouter",
    "BanditStateRegistry",
    "PIDConfig",
    "PIDDiagnostics",
    "PIDState",
    "PIDStepResult",
    "RouterConfig",
    "RoutingResult",
    "calculate_gamma_from_half_life",
    "calculate_pid_step",
    "project_to_bounded_simplex",
]
