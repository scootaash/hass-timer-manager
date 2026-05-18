# Voice Timers

Surface Home Assistant Assist voice timers as dashboard sensor entities.

## What this does

Every time you say "set a 5 minute pasta timer" on a Voice PE or any Assist satellite, this integration creates a sensor entity (`sensor.voice_timer_<uuid>`) with attributes that [timer-bar-card](https://github.com/rianadon/timer-bar-card) can use to draw a live countdown bar.

- Per-timer sensors with `started_at` and `finishes_at` — no polling needed.
- Summary sensor `sensor.voice_timers_active` for conditional dashboard sections.
- Four control services: `pause`, `unpause`, `cancel`, `add_time`.
- The Voice PE LED ring and native chimes keep working; the integration wraps (does not replace) the existing handler.

## Requirements

- Home Assistant ≥ 2024.6.0
- [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) (HACS frontend)
- [timer-bar-card](https://github.com/rianadon/timer-bar-card) (HACS frontend)

## Quick setup

1. Install via HACS → Integrations → search "Voice Timers".
2. Add the integration in **Settings → Devices & Services**.
3. See the README for the dashboard YAML.
