"""Router core package for dynamic payment routing."""

from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import (
    PIDConfig,
    PIDDiagnostics,
    PIDState,
    PIDStepResult,
    calculate_pid_step,
    project_to_bounded_simplex,
)
from router_core.policy import (
    Decision,
    DecisionContext,
    FailureKind,
    LoomPolicy,
    Observation,
    RoutingPolicy,
)
from router_core.router import BanditRouter
from router_core.state import (
    AcquirerState,
    AcquirerStateConfig,
    AcquirerStateSnapshot,
)
from router_core.value_policy import (
    ValueScaledExplorationConfig,
    apply_value_scaled_policy,
)

__all__ = [
    "AcquirerRouteConfig",
    "AcquirerState",
    "AcquirerStateConfig",
    "AcquirerStateSnapshot",
    "BanditRouter",
    "BanditStateRegistry",
    "Decision",
    "DecisionContext",
    "FailureKind",
    "LoomPolicy",
    "Observation",
    "PIDConfig",
    "PIDDiagnostics",
    "PIDState",
    "PIDStepResult",
    "RouterConfig",
    "RoutingPolicy",
    "RoutingResult",
    "ValueScaledExplorationConfig",
    "apply_value_scaled_policy",
    "calculate_pid_step",
    "project_to_bounded_simplex",
]
