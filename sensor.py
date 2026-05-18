"""Sensor platform for Voice Timers.

One sensor is spawned per active voice timer. The entity exposes
``started_at`` and ``finishes_at`` attributes so timer-bar-card can draw
a smooth countdown without depending on entity state polling.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_TIMER

_LOGGER = logging.getLogger(__name__)

# How long to keep a finished or cancelled timer visible before removing it.
LINGER_SECONDS = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Subscribe to timer events and spawn sensors on demand."""
    entities: dict[str, VoiceTimerSensor] = {}

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
        self._started_at = now
        self._finishes_at = now + timedelta(seconds=self._seconds_left)

        self._attr_unique_id = f"voice_timer_{self._timer_id}"
        self._attr_name = self._label or f"Voice timer {self._timer_id[:6]}"
        if self._device_id:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self._device_id)},
                name="Voice timer host",
                manufacturer="Home Assistant",
            )

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
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
        self._is_active = False
        self._seconds_left = 0
        self._state = kind  # 'finished' or 'cancelled'
        self.async_write_ha_state()
        try:
            await asyncio.sleep(LINGER_SECONDS)
        finally:
            await self.async_remove(force_remove=True)


def _fmt(seconds) -> str:
    s = max(0, int(seconds or 0))
    hours, rem = divmod(s, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
