"""Voice Timers: surface Assist voice timers as dashboard sensors.

Wraps each registered timer handler on Home Assistant's TimerManager so
device-side behaviour (e.g. the Voice PE LED ring countdown) is preserved
while also firing a Home Assistant event that the sensor platform turns
into per-timer entities.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, EVENT_TIMER

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

_WRAPPED_MARKER = "__voice_timers_wrapped__"
_PATCHED_MARKER = "__voice_timers_patched__"

SERVICE_PAUSE = "pause"
SERVICE_UNPAUSE = "unpause"
SERVICE_CANCEL = "cancel"
SERVICE_ADD_TIME = "add_time"

_TIMER_ID_SCHEMA = vol.Schema({vol.Required("timer_id"): cv.string})
_ADD_TIME_SCHEMA = vol.Schema(
    {
        vol.Required("timer_id"): cv.string,
        vol.Required("seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=-3600, max=3600)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Voice Timers from a config entry."""
    # Lazy import — if the class moves between HA versions we fall back to duck typing.
    try:
        from homeassistant.components.intent.timers import TimerManager as _TM
    except (ImportError, Exception):
        _TM = None

    _DUCK_ATTRS = ("handlers", "pause_timer", "cancel_timer", "register_handler")

    # Find the TimerManager that the intent integration creates.
    timer_manager: Any | None = None
    for value in hass.data.values():
        if _TM is not None:
            if isinstance(value, _TM):
                timer_manager = value
                break
        else:
            if all(hasattr(value, a) for a in _DUCK_ATTRS):
                timer_manager = value
                break

    if timer_manager is None:
        _LOGGER.error(
            "TimerManager not found in hass.data; is the intent integration loaded?"
        )
        return False

    # The handlers dict is currently public (`handlers`) but fall back to the
    # private name if core flips it.
    handlers = getattr(timer_manager, "handlers", None)
    if handlers is None:
        handlers = getattr(timer_manager, "_handlers", None)
    if handlers is None:
        _LOGGER.error("Could not locate a handlers dict on TimerManager")
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["manager"] = timer_manager
    hass.data[DOMAIN]["originals"] = {}

    @callback
    def fire_event(device_id: str, event_type: Any, timer_info: Any) -> None:
        kind = event_type.value if hasattr(event_type, "value") else str(event_type)
        payload: dict[str, Any] = {
            "device_id": device_id,
            "event_type": kind,
            "timer_id": timer_info.id,
            "name": timer_info.name or "",
            "seconds": timer_info.seconds,
            "seconds_left": getattr(timer_info, "seconds_left", timer_info.seconds),
            "is_active": timer_info.is_active,
            "start_hours": timer_info.start_hours,
            "start_minutes": timer_info.start_minutes,
            "start_seconds": timer_info.start_seconds,
        }
        hass.bus.async_fire(EVENT_TIMER, payload)

    @callback
    def wrap(device_id: str, original):
        @callback
        def wrapper(event_type, timer_info) -> None:
            try:
                original(event_type, timer_info)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Original timer handler raised for device %s", device_id
                )
            fire_event(device_id, event_type, timer_info)

        setattr(wrapper, _WRAPPED_MARKER, True)
        return wrapper

    # Wrap handlers that satellites have already registered.
    for device_id, handler in list(handlers.items()):
        if getattr(handler, _WRAPPED_MARKER, False):
            continue
        hass.data[DOMAIN]["originals"][device_id] = handler
        handlers[device_id] = wrap(device_id, handler)
        _LOGGER.debug("Wrapped existing timer handler for %s", device_id)

    # Patch register_handler so any future registration (e.g. a satellite
    # added after we set up) is wrapped automatically.
    original_register = getattr(timer_manager, "register_handler", None)
    if original_register is not None and not getattr(
        original_register, _PATCHED_MARKER, False
    ):

        @callback
        def patched_register(device_id: str, handler) -> Any:
            unregister = original_register(device_id, handler)
            current = handlers.get(device_id)
            if current is handler:
                hass.data[DOMAIN]["originals"][device_id] = handler
                handlers[device_id] = wrap(device_id, handler)
                _LOGGER.debug("Wrapped new timer handler for %s", device_id)
            return unregister

        setattr(patched_register, _PATCHED_MARKER, True)
        timer_manager.register_handler = patched_register
        hass.data[DOMAIN]["original_register"] = original_register

    _register_services(hass, timer_manager)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _register_services(hass: HomeAssistant, manager: Any) -> None:
    """Register the four voice_timers control services."""

    async def handle_pause(call: ServiceCall) -> None:
        timer_id: str = call.data["timer_id"]
        try:
            manager.pause_timer(timer_id)
        except Exception as err:
            raise ServiceValidationError(
                f"Could not pause timer {timer_id}: {err}"
            ) from err

    async def handle_unpause(call: ServiceCall) -> None:
        timer_id: str = call.data["timer_id"]
        try:
            manager.unpause_timer(timer_id)
        except Exception as err:
            raise ServiceValidationError(
                f"Could not unpause timer {timer_id}: {err}"
            ) from err

    async def handle_cancel(call: ServiceCall) -> None:
        timer_id: str = call.data["timer_id"]
        try:
            manager.cancel_timer(timer_id)
        except Exception as err:
            raise ServiceValidationError(
                f"Could not cancel timer {timer_id}: {err}"
            ) from err

    async def handle_add_time(call: ServiceCall) -> None:
        timer_id: str = call.data["timer_id"]
        seconds: int = int(call.data["seconds"])
        try:
            if seconds >= 0:
                manager.add_time(timer_id, seconds)
            else:
                # TimerManager has a separate remove_time for shrinking.
                manager.remove_time(timer_id, abs(seconds))
        except Exception as err:
            raise ServiceValidationError(
                f"Could not adjust timer {timer_id} by {seconds}s: {err}"
            ) from err

    hass.services.async_register(
        DOMAIN, SERVICE_PAUSE, handle_pause, schema=_TIMER_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNPAUSE, handle_unpause, schema=_TIMER_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL, handle_cancel, schema=_TIMER_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TIME, handle_add_time, schema=_ADD_TIME_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Restore original handlers and unload the sensor platform."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    store = hass.data.get(DOMAIN, {})
    manager = store.get("manager")
    if manager is not None:
        handlers = getattr(manager, "handlers", None) or getattr(
            manager, "_handlers", None
        )
        if handlers is not None:
            for device_id, original in store.get("originals", {}).items():
                handlers[device_id] = original
        original_register = store.get("original_register")
        if original_register is not None:
            manager.register_handler = original_register

    for service_name in (SERVICE_PAUSE, SERVICE_UNPAUSE, SERVICE_CANCEL, SERVICE_ADD_TIME):
        hass.services.async_remove(DOMAIN, service_name)

    hass.data.pop(DOMAIN, None)
    return True
