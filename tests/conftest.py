"""Shared fixtures for Voice Timers tests."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_timers.const import DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow the test harness to load custom integrations."""
    yield


class StubTimerInfo:
    """Minimal stand-in for homeassistant.components.intent.TimerInfo."""

    def __init__(
        self,
        timer_id: str,
        name: str = "",
        seconds: int = 300,
        seconds_left: int | None = None,
        is_active: bool = True,
    ) -> None:
        self.id = timer_id
        self.name = name
        self.seconds = seconds
        self.seconds_left = seconds_left if seconds_left is not None else seconds
        self.is_active = is_active
        self.start_hours = 0
        self.start_minutes = 5
        self.start_seconds = 0


class StubTimerManager:
    """Minimal stand-in for homeassistant.components.intent.timers.TimerManager."""

    def __init__(self) -> None:
        self.handlers: dict = {}
        self.pause_timer = MagicMock()
        self.unpause_timer = MagicMock()
        self.cancel_timer = MagicMock()
        self.add_time = MagicMock()
        self.remove_time = MagicMock()

    def register_handler(self, device_id: str, handler) -> object:
        self.handlers[device_id] = handler

        def unregister() -> None:
            self.handlers.pop(device_id, None)

        return unregister


@pytest.fixture
def stub_manager() -> StubTimerManager:
    return StubTimerManager()


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Voice Timers", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    return entry
