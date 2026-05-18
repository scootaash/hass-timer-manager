# Voice Timers

Surface Home Assistant Assist voice timers (Voice PE, Assist on phones, etc.) as dynamic sensor entities so they show up on the dashboard while the on-device behaviour (LED countdown, native chime) keeps working.

## How it works

The `intent` integration owns a `TimerManager` that runs every Assist voice timer in Python. Each device that supports timers registers a handler callback that gets fired on `started`, `updated`, `cancelled` and `finished` events. Voice PE's handler is what drives the LED ring.

This integration wraps each registered handler. The original is still called (so the LED ring still does its countdown and the device still chimes), and additionally a `voice_timers_event` is fired on the HA event bus. A sensor platform listens to those events and spawns one entity per active timer, removing it 30 seconds after finish/cancel.

## Install

1. Copy the `voice_timers` folder to `/config/custom_components/voice_timers/`.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → Voice Timers.

## Dashboard

Requires `auto-entities` and `timer-bar-card` from HACS.

```yaml
type: custom:auto-entities
show_empty: false
card:
  type: custom:timer-bar-card
  name: Voice timers
  compressed: true
  active_state:
    - active
  start_time:
    attribute: started_at
  end_time:
    attribute: finishes_at
  bar_foreground: var(--accent-color)
  text_width: 5em
  modifications:
    - elapsed: 75%
      bar_foreground: orange
    - elapsed: 95%
      bar_foreground: red
filter:
  include:
    - entity_id: sensor.voice_timer_*
      options:
        name: this.attributes.friendly_label
```

## Event payload

For automations that need to react to a timer ending (e.g. an extra chime on a Sonos):

```yaml
trigger:
  - platform: event
    event_type: voice_timers_event
    event_data:
      event_type: finished
```

Event data fields: `device_id`, `event_type` (`started` / `updated` / `cancelled` / `finished`), `timer_id`, `name`, `seconds`, `seconds_left`, `is_active`, `start_hours`, `start_minutes`, `start_seconds`.

## Caveats

This integration touches a TimerManager attribute (`handlers` or `_handlers`) that core treats as internal. If the core layout changes, the wrapping may need updating; `async_unload_entry` cleanly restores originals so a broken update is easy to roll back. The integration also patches `register_handler` so satellites that come online after setup are wrapped too.
