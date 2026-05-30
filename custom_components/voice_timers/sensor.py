"""Sensor platform for Voice Timers.

Two entity types:
- VoiceTimerSensor: one instance per active timer, keyed by timer_id. Exposes
  started_at / finishes_at attributes so timer-bar-card can draw a smooth
  countdown without depending on entity state polling.
- VoiceTimersActiveSensor: singleton summary; state is the integer count of
  currently active timers.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
try:
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    from homeassistant.helpers.entity import DeviceInfo  # type: ignore[no-redef]
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_TIMER, LINGER_SECONDS, LINGER_SECONDS_FINISHED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Subscribe to timer events and spawn sensors on demand."""
    # Remove any stale per-timer entities left in the registry from a previous
    # session. Older versions set unique_id on VoiceTimerSensor, which caused
    # every finished timer to linger as "Unavailable" forever.
    ent_reg = er.async_get(hass)
    stale = [
        reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if (reg_entry.unique_id or "").startswith("voice_timer_")
        and reg_entry.unique_id != "voice_timers_active"
    ]
    for entity_id in stale:
        ent_reg.async_remove(entity_id)

    entities: dict[str, VoiceTimerSensor] = {}

    summary = VoiceTimersActiveSensor()
    async_add_entities([summary])

    @callback
    def handle_event(event) -> None:
        data = event.data
        timer_id = data.get("timer_id")
        kind = data.get("event_type")
        if timer_id is None or kind is None:
            return

        if kind == "started":
            existing = entities.get(timer_id)
            if existing is not None:
                existing.update_from(data)
                return
            entity = VoiceTimerSensor(data)
            entities[timer_id] = entity
            async_add_entities([entity])
            return

        if kind == "updated":
            entity = entities.get(timer_id)
            if entity is not None:
                entity.update_from(data)
            return

        if kind in ("cancelled", "finished"):
            entity = entities.pop(timer_id, None)
            if entity is not None:
                hass.async_create_task(entity.finalise(kind))
            return

    entry.async_on_unload(hass.bus.async_listen(EVENT_TIMER, handle_event))

    # On startup: clean stale registry entries and bootstrap sensors for timers
    # that were already running before this integration loaded.
    manager = hass.data.get(DOMAIN, {}).get("manager")
    if manager is not None:
        existing_timers: dict = (
            getattr(manager, "timers", None) or getattr(manager, "_timers", None) or {}
        )
        if not isinstance(existing_timers, dict):
            existing_timers = {}

        active_ids = set(existing_timers.keys())

        # Remove entity registry entries for timers that no longer exist.
        # These are left behind when HA restarts during or after a timer's
        # 30-second linger period, showing as "unavailable" on the dashboard.
        ent_reg = er.async_get(hass)
        for reg_entry in list(ent_reg.entities.values()):
            if (
                reg_entry.platform == DOMAIN
                and reg_entry.unique_id.startswith("voice_timer_")
                and reg_entry.unique_id.removeprefix("voice_timer_") not in active_ids
            ):
                ent_reg.async_remove(reg_entry.entity_id)
                _LOGGER.debug("Removed stale timer entity %s", reg_entry.entity_id)

        # Create entities for timers that started before this integration loaded.
        bootstrap: list[VoiceTimerSensor] = []
        for timer_id, timer_info in existing_timers.items():
            if timer_id in entities:
                continue
            payload = {
                "device_id": getattr(timer_info, "device_id", "") or "",
                "event_type": "started",
                "timer_id": getattr(timer_info, "id", timer_id),
                "name": getattr(timer_info, "name", "") or "",
                "seconds": getattr(timer_info, "seconds", 0),
                "seconds_left": getattr(
                    timer_info, "seconds_left", getattr(timer_info, "seconds", 0)
                ),
                "is_active": getattr(timer_info, "is_active", True),
                "start_hours": getattr(timer_info, "start_hours", 0),
                "start_minutes": getattr(timer_info, "start_minutes", 0),
                "start_seconds": getattr(timer_info, "start_seconds", 0),
            }
            entity = VoiceTimerSensor(payload)
            entities[timer_id] = entity
            bootstrap.append(entity)
        if bootstrap:
            async_add_entities(bootstrap)


class VoiceTimerSensor(SensorEntity):
    """A single voice timer surfaced as a sensor entity."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:timer-sand"
    _attr_should_poll = False

    def __init__(self, payload: dict) -> None:
        self._timer_id: str = payload["timer_id"]
        self._device_id: str = payload.get("device_id") or ""
        self._label: str = payload.get("name") or "Timer"
        self._seconds: int = int(payload["seconds"] or 0)
        self._seconds_left: int = int(
            payload.get("seconds_left") or payload["seconds"] or 0
        )
        self._is_active: bool = bool(payload["is_active"])
        self._state: str = "active" if self._is_active else "paused"

        now = dt_util.utcnow()
        elapsed = max(0, self._seconds - self._seconds_left)
        self._started_at = now - timedelta(seconds=elapsed)
        self._finishes_at = now + timedelta(seconds=self._seconds_left)

        self._attr_name = (
            self._label.capitalize() if self._label else f"Voice timer {self._timer_id[:6]}"
        )
        # Set entity_id explicitly so it matches sensor.voice_timer_<uuid>
        # regardless of the timer name.
        self.entity_id = f"sensor.voice_timer_{self._timer_id}"

        if self._device_id:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self._device_id)},
                name="Voice timer host",
                manufacturer="Home Assistant",
            )

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "timer_id": self._timer_id,
            "device_id": self._device_id,
            "friendly_label": self._label,
            "duration": _fmt(self._seconds),
            "remaining": _fmt(self._seconds_left),
            "started_at": self._started_at.isoformat(),
            "finishes_at": self._finishes_at.isoformat(),
            "seconds_total": self._seconds,
            "seconds_left": self._seconds_left,
        }

    @callback
    def update_from(self, payload: dict) -> None:
        """Apply a 'started' (restart) or 'updated' payload."""
        self._seconds = int(payload["seconds"] or 0)
        self._seconds_left = int(
            payload.get("seconds_left") or payload["seconds"] or 0
        )
        self._is_active = bool(payload["is_active"])
        self._state = "active" if self._is_active else "paused"
        self._finishes_at = dt_util.utcnow() + timedelta(seconds=self._seconds_left)
        self.async_write_ha_state()

    async def finalise(self, kind: str) -> None:
        """Hold the entity at the terminal state briefly, then remove."""
        linger = LINGER_SECONDS_FINISHED if kind == "finished" else LINGER_SECONDS
        self._is_active = False
        self._seconds_left = 0
        self._state = kind  # 'finished' or 'cancelled'
        self.async_write_ha_state()
        try:
            await asyncio.sleep(linger)
        finally:
            hass = self.hass
            entity_id = self.entity_id
            await self.async_remove(force_remove=True)
            # async_remove does not remove the entity registry entry, so stale
            # "unavailable" sensors accumulate after HA restart. Remove it explicitly.
            ent_reg = er.async_get(hass)
            if ent_reg.async_get(entity_id) is not None:
                ent_reg.async_remove(entity_id)


class VoiceTimersActiveSensor(SensorEntity):
    """Singleton summary sensor: count of currently active voice timers."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:timer-multiple"
    _attr_should_poll = False
    _attr_unique_id = "voice_timers_active"
    _attr_name = "Voice timers active"

    def __init__(self) -> None:
        self._timer_ids: set[str] = set()
        self._by_device: dict[str, int] = {}
        self._device_of: dict[str, str] = {}
        self.entity_id = "sensor.voice_timers_active"

    @property
    def native_value(self) -> int:
        return len(self._timer_ids)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "timer_ids": sorted(self._timer_ids),
            "by_device": dict(self._by_device),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_TIMER, self._handle_event)
        )
        # Bootstrap count from timers already active when the integration loads.
        manager = self.hass.data.get(DOMAIN, {}).get("manager")
        if manager is not None:
            existing_timers: dict = (
                getattr(manager, "timers", None)
                or getattr(manager, "_timers", None)
                or {}
            )
            if isinstance(existing_timers, dict) and existing_timers:
                for timer_id, timer_info in existing_timers.items():
                    device_id: str = getattr(timer_info, "device_id", "") or ""
                    self._timer_ids.add(timer_id)
                    self._device_of[timer_id] = device_id
                    self._by_device[device_id] = self._by_device.get(device_id, 0) + 1
                self.async_write_ha_state()

    @callback
    def _handle_event(self, event) -> None:
        data = event.data
        timer_id: str | None = data.get("timer_id")
        kind: str | None = data.get("event_type")
        device_id: str = data.get("device_id") or ""

        if timer_id is None or kind is None:
            return

        if kind == "started":
            if timer_id not in self._timer_ids:
                self._timer_ids.add(timer_id)
                self._device_of[timer_id] = device_id
                self._by_device[device_id] = self._by_device.get(device_id, 0) + 1
        elif kind in ("cancelled", "finished"):
            if timer_id in self._timer_ids:
                self._timer_ids.discard(timer_id)
                dev = self._device_of.pop(timer_id, device_id)
                self._by_device[dev] = max(0, self._by_device.get(dev, 1) - 1)
                if self._by_device[dev] == 0:
                    del self._by_device[dev]

        self.async_write_ha_state()


def _fmt(seconds) -> str:
    s = max(0, int(seconds or 0))
    hours, rem = divmod(s, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
