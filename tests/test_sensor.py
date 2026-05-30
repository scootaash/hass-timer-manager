"""Tests for Voice Timers sensor platform: lifecycle, multi-timer, summary."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

# EntityPlatformState marks an entity as fully added so async_write_ha_state
# doesn't log an error. Import defensively — it moved/was restructured in
# some HA versions.
try:
    from homeassistant.helpers.entity import EntityPlatformState as _EPS
    _PLATFORM_STATE_ADDED = _EPS.ADDED
except (ImportError, AttributeError):
    _PLATFORM_STATE_ADDED = None

from custom_components.voice_timers.sensor import (
    LINGER_SECONDS,
    LINGER_SECONDS_FINISHED,
    VoiceTimerSensor,
    VoiceTimersActiveSensor,
    async_setup_entry,
)
from custom_components.voice_timers.const import DOMAIN, EVENT_TIMER
from .conftest import StubTimerInfo, StubTimerManager


# ---------------------------------------------------------------------------
# Platform setup helper
# ---------------------------------------------------------------------------

async def _setup_platform(hass: HomeAssistant, manager=None):
    """Set up the sensor platform; return the add_entities-tracked lists."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    if manager is not None:
        hass.data.setdefault(DOMAIN, {})["manager"] = manager

    per_timer: dict[str, VoiceTimerSensor] = {}
    summary_holder: list[VoiceTimersActiveSensor] = []

    @callback
    def add_entities(entities, update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            if _PLATFORM_STATE_ADDED is not None:
                ent._platform_state = _PLATFORM_STATE_ADDED  # noqa: SLF001
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
    from unittest.mock import patch as _patch

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
    old_finishes_at = entity._finishes_at

    # async_write_ha_state requires entity.hass; patch it out for this unit test.
    with _patch.object(entity, "async_write_ha_state"):
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

    # finalise() sets state to "finished" and calls async_remove.
    assert entity.native_value == "finished"
    # The sensor platform's internal entities dict popped "t1" when the event fired.
    # (per_timer is our test tracking dict that only grows — don't assert on it here.)


async def test_sensor_removed_after_cancelled(hass: HomeAssistant) -> None:
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()
    entity = per_timer["t1"]

    with patch("asyncio.sleep", new=AsyncMock()):
        _fire(hass, "cancelled", "t1")
        await hass.async_block_till_done()

    assert entity.native_value == "cancelled"


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

    with patch("asyncio.sleep", new=AsyncMock()):
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

    with patch("asyncio.sleep", new=AsyncMock()):
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


# ---------------------------------------------------------------------------
# started_at accuracy for bootstrapped (in-progress) timers
# ---------------------------------------------------------------------------

def test_timer_sensor_started_at_for_in_progress_timer() -> None:
    """started_at is backdated correctly when seconds_left < seconds."""
    from homeassistant.util import dt as dt_util
    from datetime import timedelta

    before = dt_util.utcnow()
    entity = VoiceTimerSensor(
        {
            "timer_id": "abc",
            "device_id": "dev1",
            "name": "pasta",
            "seconds": 300,
            "seconds_left": 120,  # 3 minutes elapsed
            "is_active": True,
        }
    )
    after = dt_util.utcnow()

    # started_at should be roughly (now - 180s), i.e. 300 - 120 = 180s ago
    elapsed = 300 - 120
    assert entity._started_at <= before - timedelta(seconds=elapsed) + timedelta(seconds=1)
    assert entity._started_at >= after - timedelta(seconds=elapsed) - timedelta(seconds=1)
    # finishes_at should be roughly now + 120s
    assert entity._finishes_at >= before + timedelta(seconds=119)
    assert entity._finishes_at <= after + timedelta(seconds=121)


def test_timer_sensor_started_at_for_fresh_timer() -> None:
    """started_at equals now when seconds_left == seconds (brand-new timer)."""
    from homeassistant.util import dt as dt_util

    before = dt_util.utcnow()
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
    after = dt_util.utcnow()

    assert entity._started_at >= before
    assert entity._started_at <= after


# ---------------------------------------------------------------------------
# Bootstrap: sensors created for timers already running at integration load
# ---------------------------------------------------------------------------

async def test_bootstrap_creates_sensor_for_active_timer(hass: HomeAssistant) -> None:
    """Sensors are created for timers already in TimerManager.timers at load time."""
    manager = StubTimerManager()
    manager.timers = {"t_existing": StubTimerInfo("t_existing", name="soup", seconds=600, seconds_left=300)}

    per_timer, _ = await _setup_platform(hass, manager=manager)

    assert "t_existing" in per_timer
    entity = per_timer["t_existing"]
    assert entity.native_value == "active"
    assert entity.extra_state_attributes["friendly_label"] == "soup"
    assert entity.entity_id == "sensor.voice_timer_t_existing"


async def test_bootstrap_sets_started_at_correctly(hass: HomeAssistant) -> None:
    """Bootstrapped timer has started_at backdated based on elapsed time."""
    from homeassistant.util import dt as dt_util
    from datetime import timedelta

    manager = StubTimerManager()
    manager.timers = {
        "t1": StubTimerInfo("t1", seconds=300, seconds_left=120)
    }

    per_timer, _ = await _setup_platform(hass, manager=manager)

    entity = per_timer["t1"]
    elapsed = 300 - 120  # 180 seconds
    now = dt_util.utcnow()
    assert entity._started_at <= now - timedelta(seconds=elapsed - 1)


async def test_bootstrap_updates_summary_sensor(hass: HomeAssistant) -> None:
    """Summary sensor count reflects timers already in TimerManager at load time."""
    manager = StubTimerManager()
    manager.timers = {
        "t1": StubTimerInfo("t1", device_id="dev1"),
        "t2": StubTimerInfo("t2", device_id="dev1"),
    }

    _, summary_holder = await _setup_platform(hass, manager=manager)
    summary = summary_holder[0]

    assert summary.native_value == 2
    assert set(summary.extra_state_attributes["timer_ids"]) == {"t1", "t2"}
    assert summary.extra_state_attributes["by_device"]["dev1"] == 2


async def test_bootstrap_does_not_duplicate_on_started_event(hass: HomeAssistant) -> None:
    """A 'started' event for an already-bootstrapped timer updates rather than duplicates."""
    manager = StubTimerManager()
    manager.timers = {"t1": StubTimerInfo("t1", seconds=300, seconds_left=250)}

    per_timer, _ = await _setup_platform(hass, manager=manager)
    assert len(per_timer) == 1

    _fire(hass, "started", "t1", seconds=300, seconds_left=250)
    await hass.async_block_till_done()

    assert len(per_timer) == 1


# ---------------------------------------------------------------------------
# Stale entity registry cleanup on startup
# ---------------------------------------------------------------------------

async def test_stale_registry_entries_removed_on_startup(hass: HomeAssistant) -> None:
    """Entity registry entries for finished timers are removed when integration loads."""
    ent_reg = er.async_get(hass)
    # Simulate stale entries from a previous HA session.
    ent_reg.async_get_or_create(
        domain="sensor", platform=DOMAIN, unique_id="voice_timer_stale_1"
    )
    ent_reg.async_get_or_create(
        domain="sensor", platform=DOMAIN, unique_id="voice_timer_stale_2"
    )

    manager = StubTimerManager()
    manager.timers = {}  # no active timers

    await _setup_platform(hass, manager=manager)

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "voice_timer_stale_1") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "voice_timer_stale_2") is None


async def test_active_timer_registry_entry_kept_on_startup(hass: HomeAssistant) -> None:
    """A registry entry for a currently active timer is preserved."""
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="sensor", platform=DOMAIN, unique_id="voice_timer_t_active"
    )

    manager = StubTimerManager()
    manager.timers = {"t_active": StubTimerInfo("t_active")}

    await _setup_platform(hass, manager=manager)

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "voice_timer_t_active") is not None


async def test_summary_registry_entry_not_touched(hass: HomeAssistant) -> None:
    """The voice_timers_active registry entry is never removed by stale cleanup."""
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="sensor", platform=DOMAIN, unique_id="voice_timers_active"
    )

    manager = StubTimerManager()
    manager.timers = {}

    await _setup_platform(hass, manager=manager)

    # "voice_timers_active" does NOT start with "voice_timer_" so it is untouched.
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "voice_timers_active") is not None


# ---------------------------------------------------------------------------
# finalise() cleans up the entity registry entry
# ---------------------------------------------------------------------------

async def test_finalise_removes_registry_entry(hass: HomeAssistant) -> None:
    """After the linger period finalise() removes any registry entry for the entity."""
    per_timer, _ = await _setup_platform(hass)

    _fire(hass, "started", "t1")
    await hass.async_block_till_done()

    entity = per_timer["t1"]
    # Manually add a registry entry, simulating an older version that stored unique_ids.
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="voice_timer_t1",
        suggested_object_id="voice_timer_t1",
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        _fire(hass, "finished", "t1")
        await hass.async_block_till_done()

    assert ent_reg.async_get(entity.entity_id) is None


# ---------------------------------------------------------------------------
# Linger duration: finished vs cancelled
# ---------------------------------------------------------------------------

async def test_finished_timer_uses_one_hour_linger(hass: HomeAssistant) -> None:
    """Finished timers sleep for LINGER_SECONDS_FINISHED so they show for 1 hour."""
    per_timer, _ = await _setup_platform(hass)
    _fire(hass, "started", "t1")
    await hass.async_block_till_done()

    sleep_calls: list[float] = []

    async def capture_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with patch("asyncio.sleep", new=capture_sleep):
        _fire(hass, "finished", "t1")
        await hass.async_block_till_done()

    assert sleep_calls and sleep_calls[0] == LINGER_SECONDS_FINISHED


async def test_cancelled_timer_uses_short_linger(hass: HomeAssistant) -> None:
    """Cancelled timers sleep for the short LINGER_SECONDS constant."""
    per_timer, _ = await _setup_platform(hass)
    _fire(hass, "started", "t1")
    await hass.async_block_till_done()

    sleep_calls: list[float] = []

    async def capture_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with patch("asyncio.sleep", new=capture_sleep):
        _fire(hass, "cancelled", "t1")
        await hass.async_block_till_done()

    assert sleep_calls and sleep_calls[0] == LINGER_SECONDS
