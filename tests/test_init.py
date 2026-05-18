"""Tests for Voice Timers __init__: handler wrapping, services, unload."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.voice_timers import async_setup_entry, async_unload_entry
from custom_components.voice_timers.const import DOMAIN, EVENT_TIMER

from .conftest import StubTimerInfo, StubTimerManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup(
    hass: HomeAssistant,
    config_entry,
    stub_manager: StubTimerManager,
) -> bool:
    """Call async_setup_entry with the sensor platform stubbed out."""
    hass.data["_test_stub"] = stub_manager
    with patch(
        "homeassistant.components.intent.timers.TimerManager",
        type(stub_manager),
    ), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        return await async_setup_entry(hass, config_entry)


async def _unload(
    hass: HomeAssistant,
    config_entry,
) -> bool:
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        return await async_unload_entry(hass, config_entry)


# ---------------------------------------------------------------------------
# Handler wrapping
# ---------------------------------------------------------------------------

async def test_existing_handler_is_wrapped(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """A handler already in the dict is replaced with a wrapped version on setup."""
    original = MagicMock()
    stub_manager.handlers["dev1"] = original

    result = await _setup(hass, config_entry, stub_manager)

    assert result is True
    wrapped = stub_manager.handlers["dev1"]
    assert wrapped is not original
    assert getattr(wrapped, "__voice_timers_wrapped__", False)


async def test_wrapped_handler_calls_original(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """The wrapped handler forwards every call to the original."""
    original = MagicMock()
    stub_manager.handlers["dev1"] = original

    await _setup(hass, config_entry, stub_manager)

    info = StubTimerInfo("t1", name="pasta")
    stub_manager.handlers["dev1"]("started", info)

    original.assert_called_once_with("started", info)


async def test_wrapped_handler_fires_event(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """The wrapped handler fires voice_timers_event on the HA bus."""
    stub_manager.handlers["dev1"] = MagicMock()

    await _setup(hass, config_entry, stub_manager)

    received: list = []
    hass.bus.async_listen(EVENT_TIMER, lambda e: received.append(e))

    info = StubTimerInfo("t1", name="pasta")
    stub_manager.handlers["dev1"]("started", info)
    await hass.async_block_till_done()

    assert len(received) == 1
    assert received[0].data["timer_id"] == "t1"
    assert received[0].data["event_type"] == "started"
    assert received[0].data["name"] == "pasta"
    assert received[0].data["device_id"] == "dev1"


# ---------------------------------------------------------------------------
# Unload / reversibility
# ---------------------------------------------------------------------------

async def test_unload_restores_original_handler(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """After unload the original handler is back in the handlers dict."""
    original = MagicMock()
    stub_manager.handlers["dev1"] = original

    await _setup(hass, config_entry, stub_manager)
    assert stub_manager.handlers["dev1"] is not original

    result = await _unload(hass, config_entry)

    assert result is True
    assert stub_manager.handlers["dev1"] is original


async def test_unload_reverts_register_handler_patch(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """After unload, register_handler is no longer our patched version."""
    original_register = stub_manager.register_handler

    await _setup(hass, config_entry, stub_manager)
    assert stub_manager.register_handler is not original_register

    await _unload(hass, config_entry)
    assert stub_manager.register_handler is original_register


async def test_no_event_fired_after_unload(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """Firing a handler after unload should not produce voice_timers_event."""
    stub_manager.handlers["dev1"] = MagicMock()
    await _setup(hass, config_entry, stub_manager)
    await _unload(hass, config_entry)

    received: list = []
    hass.bus.async_listen(EVENT_TIMER, lambda e: received.append(e))

    # Call the original (restored) handler directly — it's a MagicMock, won't fire.
    stub_manager.handlers["dev1"]("started", StubTimerInfo("t1"))
    await hass.async_block_till_done()

    assert received == []


# ---------------------------------------------------------------------------
# Late registration
# ---------------------------------------------------------------------------

async def test_late_registration_is_wrapped(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """A handler registered via register_handler after setup is auto-wrapped."""
    await _setup(hass, config_entry, stub_manager)

    late = MagicMock()
    stub_manager.register_handler("late_dev", late)

    wrapped = stub_manager.handlers.get("late_dev")
    assert wrapped is not None
    assert getattr(wrapped, "__voice_timers_wrapped__", False)


async def test_late_registration_fires_event(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """The wrapped late handler fires voice_timers_event."""
    await _setup(hass, config_entry, stub_manager)

    late = MagicMock()
    stub_manager.register_handler("late_dev", late)

    received: list = []
    hass.bus.async_listen(EVENT_TIMER, lambda e: received.append(e))

    info = StubTimerInfo("t2", name="laundry")
    stub_manager.handlers["late_dev"]("started", info)
    await hass.async_block_till_done()

    late.assert_called_once_with("started", info)
    assert len(received) == 1
    assert received[0].data["timer_id"] == "t2"


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

async def test_service_pause(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "pause", {"timer_id": "abc"}, blocking=True
    )
    stub_manager.pause_timer.assert_called_once_with("abc")


async def test_service_unpause(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "unpause", {"timer_id": "abc"}, blocking=True
    )
    stub_manager.unpause_timer.assert_called_once_with("abc")


async def test_service_cancel(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "cancel", {"timer_id": "abc"}, blocking=True
    )
    stub_manager.cancel_timer.assert_called_once_with("abc")


async def test_service_add_time_positive(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """Positive seconds → add_time."""
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "add_time", {"timer_id": "abc", "seconds": 60}, blocking=True
    )
    stub_manager.add_time.assert_called_once_with("abc", 60)
    stub_manager.remove_time.assert_not_called()


async def test_service_add_time_negative(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """Negative seconds → remove_time with absolute value."""
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "add_time", {"timer_id": "abc", "seconds": -30}, blocking=True
    )
    stub_manager.remove_time.assert_called_once_with("abc", 30)
    stub_manager.add_time.assert_not_called()


async def test_service_add_time_zero(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """Zero seconds → add_time(0), not remove_time."""
    await _setup(hass, config_entry, stub_manager)

    await hass.services.async_call(
        DOMAIN, "add_time", {"timer_id": "abc", "seconds": 0}, blocking=True
    )
    stub_manager.add_time.assert_called_once_with("abc", 0)
    stub_manager.remove_time.assert_not_called()


async def test_services_removed_on_unload(
    hass: HomeAssistant, config_entry, stub_manager: StubTimerManager
) -> None:
    """Services are de-registered when the entry is unloaded."""
    await _setup(hass, config_entry, stub_manager)
    assert hass.services.has_service(DOMAIN, "pause")

    await _unload(hass, config_entry)
    assert not hass.services.has_service(DOMAIN, "pause")
    assert not hass.services.has_service(DOMAIN, "unpause")
    assert not hass.services.has_service(DOMAIN, "cancel")
    assert not hass.services.has_service(DOMAIN, "add_time")
