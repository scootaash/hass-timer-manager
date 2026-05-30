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

### Classic

Requires [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) and [timer-bar-card](https://github.com/rianadon/timer-bar-card) from HACS.

```yaml
type: custom:auto-entities
show_empty: false
card:
  type: custom:timer-bar-card
  name: Voice timers
  compressed: true
  active_state: active
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
```

### Mushroom

Requires [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) and [Mushroom](https://github.com/piitaya/lovelace-mushroom) from HACS. Shows each timer as a Mushroom card with a colour-coded icon (green → orange → red as time runs out) and a pause badge when paused.

```yaml
type: custom:auto-entities
show_empty: false
filter:
  include:
    - entity_id: sensor.voice_timer_*
card:
  type: custom:mushroom-template-card
  primary: "{{ state_attr(entity, 'friendly_label') | capitalize }}"
  secondary: >-
    {% set end = state_attr(entity, 'finishes_at') | as_datetime %}
    {% if end %}
      {% set s = ((end - now()).total_seconds()) | int(0) %}
      {% set s = [s, 0] | max %}
      {{ s // 60 }}m {{ '%02d' | format(s % 60) }}s remaining
    {% endif %}
  icon: mdi:timer-sand
  icon_color: >-
    {% set s = state_attr(entity, 'seconds_left') | int(0) %}
    {% set t = state_attr(entity, 'seconds_total') | int(1) %}
    {% set elapsed = (1 - s / t) if t > 0 else 1 %}
    {% if elapsed > 0.95 %}red
    {% elif elapsed > 0.75 %}orange
    {% else %}green{% endif %}
  badge_icon: >-
    {% if states(entity) == 'paused' %}mdi:pause-circle{% endif %}
  badge_color: grey
  tap_action:
    action: none
```

### Key options

| Option | Where | Description |
|--------|-------|-------------|
| `show_empty: false` | `auto-entities` | Hide the whole card when no timers are running |
| `compressed: true` | `timer-bar-card` | Bar and time on one row instead of two |
| `text_width` | `timer-bar-card` | Width of the countdown text column (default `5em`) |
| `bar_foreground` | `timer-bar-card` | Bar fill colour — any CSS colour or variable |
| `bar_background` | `timer-bar-card` | Track colour behind the bar |
| `modifications` | `timer-bar-card` | Override bar colour at a given `elapsed` percentage |
| `active_state` | `timer-bar-card` | State value(s) the card treats as "running" (`active` for this integration) |

## Services

Four services let you control timers from the dashboard, scripts, or automations.

| Service | What it does |
|---------|-------------|
| `voice_timers.pause` | Pause a running timer |
| `voice_timers.unpause` | Resume a paused timer |
| `voice_timers.cancel` | Cancel a timer (no finished event fires) |
| `voice_timers.add_time` | Add seconds (positive) or subtract seconds (negative) |

All services accept **`entity_id`** (the `sensor.voice_timer_*` entity — shown as a dropdown in the UI) or the raw **`timer_id`** UUID for automation use.

### From the Developer Tools

**Developer Tools → Actions → voice_timers.cancel** — pick the entity from the dropdown, hit Perform Action.

### From a button card

```yaml
type: button
name: Cancel
icon: mdi:timer-off
tap_action:
  action: perform-action
  perform_action: voice_timers.cancel
  data:
    entity_id: sensor.voice_timer_<uuid>
```

### From an automation (timer finished → chime)

```yaml
trigger:
  - platform: event
    event_type: voice_timers_event
    event_data:
      event_type: finished
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.kitchen_sonos
    data:
      media_content_id: /local/chime.mp3
      media_content_type: music
```

### Add / remove time from an automation

```yaml
action:
  - service: voice_timers.add_time
    data:
      entity_id: sensor.voice_timer_<uuid>
      seconds: 120   # −120 to remove 2 minutes
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
