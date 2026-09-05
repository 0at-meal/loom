"""Unit tests for server.py CLI parsing and app.py default PID wiring."""

from __future__ import annotations

import pytest

from router_core.app import app, create_router_app
from router_core.server import build_router_config, parse_args


class TestServerCLIAndAppPIDWiring:
    """Test suite ensuring PID is enabled by default with tuned gains in CLI and app."""

    def test_default_cli_args_enable_pid_with_tuned_gains(self) -> None:
        """Default CLI arguments must have PID enabled with Phase 4 tuned gains."""
        parsed = parse_args([])
        assert parsed.pid is True
        assert parsed.kp == 0.12
        assert parsed.ki == 0.005
        assert parsed.kd == 0.25
        assert parsed.min_allocation == 0.03

        config = build_router_config(parsed)
        assert config.pid_config is not None
        assert config.pid_config.kp == 0.12
        assert config.pid_config.ki == 0.005
        assert config.pid_config.kd == 0.25
        assert config.pid_config.min_allocation == 0.03

    def test_cli_explicit_pid_flags(self) -> None:
        """Passing --pid or --no-pid should toggle pid_config in router config."""
        parsed_enabled = parse_args(["--pid"])
        assert parsed_enabled.pid is True
        cfg_enabled = build_router_config(parsed_enabled)
        assert cfg_enabled.pid_config is not None

        parsed_disabled = parse_args(["--no-pid"])
        assert parsed_disabled.pid is False
        cfg_disabled = build_router_config(parsed_disabled)
        assert cfg_disabled.pid_config is None

    def test_cli_custom_gain_overrides(self) -> None:
        """CLI arguments must allow overriding individual PID gains."""
        parsed = parse_args(
            [
                "--pid",
                "--kp",
                "0.18",
                "--ki",
                "0.02",
                "--kd",
                "0.35",
                "--min-allocation",
                "0.05",
            ]
        )
        assert parsed.pid is True
        assert parsed.kp == pytest.approx(0.18)
        assert parsed.ki == pytest.approx(0.02)
        assert parsed.kd == pytest.approx(0.35)
        assert parsed.min_allocation == pytest.approx(0.05)

        config = build_router_config(parsed)
        assert config.pid_config is not None
        assert config.pid_config.kp == pytest.approx(0.18)
        assert config.pid_config.ki == pytest.approx(0.02)
        assert config.pid_config.kd == pytest.approx(0.35)
        assert config.pid_config.min_allocation == pytest.approx(0.05)

    def test_app_default_fallback_has_pid_enabled(self) -> None:
        """Default fallback topology must enable PID with tuned gains."""
        app_instance = create_router_app()
        router = app_instance.state.router
        assert router.config.pid_config is not None
        assert router.config.pid_config.kp == 0.12
        assert router.config.pid_config.ki == 0.005
        assert router.config.pid_config.kd == 0.25
        assert router.config.pid_config.min_allocation == 0.03

    def test_module_level_app_has_pid_enabled(self) -> None:
        """The module-level ASGI app instance must enable PID with tuned gains."""
        router = app.state.router
        assert router.config.pid_config is not None
        assert router.config.pid_config.kp == 0.12
        assert router.config.pid_config.ki == 0.005
        assert router.config.pid_config.kd == 0.25
        assert router.config.pid_config.min_allocation == 0.03
