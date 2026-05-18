"""Tests for Voice Timers sensor platform: lifecycle, multi-timer, summary."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_timers.sensor import (
    LINGER_SECONDS,
    VoiceTimerSensor,
    VoiceTimersActiveSensor,
    async_setup_entry,
)
from custom_components.voice_timers.const import DOMAIN, EVENT_TIMER


# ---------------------------------------------------------------------------
# Platform setup helper
# ---------------------------------------------------------------------------

async def _setup_platform(hass: HomeAssistant):
    """Set up the sensor platform; return the add_entities-tracked lists."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    per_timer: dict[str, VoiceTimerSensor] = {}
    summary_holder: list[VoiceTimersActiveSensor] = []

    @callback
    def add_entities(entities, update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            if isinstance(ent, VoiceTimersActiveSensor):
                summary_holder.append(ent)
                # Wire up async_added_to_hass so it subscribes to the bus.
                hass.async_create_task(ent.async_added_to_hass())
            elif isinstance(ent, VoiceTimerSensor):
                per_timer[ent._timer_id] = ent  # noqa: SLF001

    await async_setup_entry(hass, entry, add_entities)
    await hass.async_block_till_done()

    return per_timer, summary_holder


def _fire(hass: HomeAssistant, event_type: str, timer_id: str = "t1", **kwargs) -> None:
    hass.bus.async_fire(
        EVENT_TIMER,
        {
            "event_type": event_type,
            "timer_id": timer_id,
            "device_id": kwargs.get("device_id", "dev1"),
            "name": kwargs.get("name", "pasta"),
            "seconds": kwargs.get("seconds", 300),
            "seconds_left": kwargs.get("seconds_left", kwargs.get("seconds", 300)),
            "is_active": kwargs.get("is_active", True),
            "start_hours": 0,
            "start_minutes": 5,
            "start_seconds": 0,
        },
    )


# ---------------------------------------------------------------------------
# VoiceTimerSensor unit tests (no HA bus needed)
# ---------------------------------------------------------------------------

def test_timer_sensor_initial_state() -> None:
    entity = VoiceTimerSensor(
        {
            "timer_id": "abc",
            "device_id": "dev1",
            "name": "pasta",
            "seconds": 300,
            "seconds_left": 300,
            "is_active": True,
        }
    )
    assert entity.native_value == "active"
    assert entity.entity_id == "sensor.voice_timer_abc"
    attrs = entity.extra_state_attributes
    assert attrs["friendly_label"] == "pasta"
    assert attrs["timer_id"] == "abc"
    assert attrs["seconds_total"] == 300
    assert attrs["duration"] == "00:05:00"


def test_timer_sensor_paused_state() -> None:
    entity = VoiceTimerSensor(
        {
            "timer_id": "abc",
            "device_id": "dev1",
            "name": "pasta",
            "seconds": 300,
            "seconds_left": 150,
            "is_active": False,
        }
    )
    assert entity.native_value == "paused"
    assert entity.extra_state_attributes["seconds_left"] == 150


def test_timer_sensor_update_from() -> None:
    entity = VoiceTimerSensor(
        {
            "timer_id": "abc",
            "device_id": "dev1",
            "name": "pasta",
            "seconds": 300,
            "seconds_left": 300,
            "is_active": True,
        }
    )
    # Simulate entity.hass being set (needed for async_write_ha_state).
    entity.hass = None  # type: ignore[assignment]

    old_finishes_at = entity._finishes_at

    entity.update_from(
        {
            "seconds": 360,
            "seconds_left": 360,
            "is_active": True,
        }
    )

    assert entity._seconds == 360
    assert entity._seconds_left == 360
    assert entity._finishes_at > old_finishes_at


# ---------------------------------------------------------------------------
# Sensor lifecycle via HA bus
# ---------------------------------------------------------------------------

async def test_sensor_created_on_started(hass: HomeAssistant) -> None:
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1", name="pasta")
    await hass.async_block_till_done()

    assert "t1" in per_timer
    entity = per_timer["t1"]
    assert entity.native_value == "active"
    assert entity.extra_state_attributes["friendly_label"] == "pasta"
    assert entity.entity_id == "sensor.voice_timer_t1"


async def test_updated_event_does_not_create_new_sensor(hass: HomeAssistant) -> None:
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()

    old_finishes = per_timer["t1"]._finishes_at

    _fire(hass, "updated", "t1", seconds=360, seconds_left=360)
    await hass.async_block_till_done()

    assert len(per_timer) == 1
    assert per_timer["t1"]._finishes_at > old_finishes


async def test_paused_state_on_updated(hass: HomeAssistant) -> None:
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()

    _fire(hass, "updated", "t1", is_active=False)
    await hass.async_block_till_done()

    assert per_timer["t1"].native_value == "paused"


async def test_sensor_removed_after_finished(hass: HomeAssistant) -> None:
    """Entity lingers as 'finished', then is removed after the sleep."""
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()
    entity = per_timer["t1"]

    with patch("asyncio.sleep", new=AsyncMock()):
        _fire(hass, "finished", "t1")
        await hass.async_block_till_done()

    assert entity.native_value == "finished"
    # Entity should have been removed from the per_timer dict before finalise was called.
    assert "t1" not in per_timer


async def test_sensor_removed_after_cancelled(hass: HomeAssistant) -> None:
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()
    entity = per_timer["t1"]

    with patch("asyncio.sleep", new=AsyncMock()):
        _fire(hass, "cancelled", "t1")
        await hass.async_block_till_done()

    assert entity.native_value == "cancelled"
    assert "t1" not in per_timer


# ---------------------------------------------------------------------------
# Multiple concurrent timers + summary sensor
# ---------------------------------------------------------------------------

async def test_multiple_concurrent_timers(hass: HomeAssistant) -> None:
    """Two started events → two separate entities."""
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1", name="pasta", device_id="dev1")
    _fire(hass, "started", "t2", name="laundry", device_id="dev1")
    await hass.async_block_till_done()

    assert len(per_timer) == 2
    assert per_timer["t1"].extra_state_attributes["friendly_label"] == "pasta"
    assert per_timer["t2"].extra_state_attributes["friendly_label"] == "laundry"


async def test_summary_sensor_initial_count(hass: HomeAssistant) -> None:
    """Summary sensor starts at 0."""
    _, summary_holder = await _setup_platform(hass)
    assert len(summary_holder) == 1
    assert summary_holder[0].native_value == 0


async def test_summary_sensor_increments_on_started(hass: HomeAssistant) -> None:
    _, summary_holder = await _setup_platform(hass)
    summary = summary_holder[0]

    _fire(hass, "started", "t1", device_id="dev1")
    _fire(hass, "started", "t2", device_id="dev1")
    await hass.async_block_till_done()

    assert summary.native_value == 2
    assert set(summary.extra_state_attributes["timer_ids"]) == {"t1", "t2"}
    assert summary.extra_state_attributes["by_device"]["dev1"] == 2


async def test_summary_sensor_decrements_on_finished(hass: HomeAssistant) -> None:
    _, summary_holder = await _setup_platform(hass)
    summary = summary_holder[0]

    _fire(hass, "started", "t1", device_id="dev1")
    _fire(hass, "started", "t2", device_id="dev1")
    await hass.async_block_till_done()
    assert summary.native_value == 2

    _fire(hass, "finished", "t1")
    await hass.async_block_till_done()
    assert summary.native_value == 1
    assert "t1" not in summary.extra_state_attributes["timer_ids"]


async def test_summary_sensor_by_device(hass: HomeAssistant) -> None:
    """by_device counts timers per device_id."""
    _, summary_holder = await _setup_platform(hass)
    summary = summary_holder[0]

    _fire(hass, "started", "t1", device_id="dev1")
    _fire(hass, "started", "t2", device_id="dev2")
    await hass.async_block_till_done()

    by_device = summary.extra_state_attributes["by_device"]
    assert by_device.get("dev1") == 1
    assert by_device.get("dev2") == 1

    _fire(hass, "cancelled", "t1", device_id="dev1")
    await hass.async_block_till_done()

    by_device = summary.extra_state_attributes["by_device"]
    assert "dev1" not in by_device
    assert by_device.get("dev2") == 1


# ---------------------------------------------------------------------------
# VoiceTimersActiveSensor unit tests
# ---------------------------------------------------------------------------

def test_summary_sensor_entity_id() -> None:
    entity = VoiceTimersActiveSensor()
    assert entity.entity_id == "sensor.voice_timers_active"
    assert entity.native_value == 0
    assert entity.extra_state_attributes["timer_ids"] == []
    assert entity.extra_state_attributes["by_device"] == {}
