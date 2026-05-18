# Voice Timers — Integration Spec

A Home Assistant custom integration that surfaces Assist voice timers (Voice PE, Assist on iOS/Android, browser-based satellites) as dynamic sensor entities, so they can be rendered on a dashboard while the on-device behaviour (LED countdown, native chime) keeps working.

## Problem

Home Assistant's Assist pipeline ships with a complete voice timer system in `homeassistant.components.intent.timers`. The `TimerManager` runs in core, fires lifecycle events to per-device handler callbacks, and powers every voice-controlled timer in the house. The Voice PE firmware registers a handler that drives its LED ring.

What's missing: there is no entity representation. Timers live entirely in memory inside `TimerManager` and never reach the entity registry. That makes them invisible to dashboards, automations, and any UI surface other than the originating device. The open feature request [esphome/home-assistant-voice-pe#387](https://github.com/esphome/home-assistant-voice-pe/issues/387) tracks this gap; it has not landed.

This integration closes that gap without modifying core.

## Scope

### In scope

- Multiple concurrent named timers per satellite, each rendered as its own sensor entity, with names taken from the voice command ("set a 5 minute **pasta** timer").
- Lifecycle: sensors appear on `started`, update on `updated` (pause, resume, increase, decrease), and disappear 30 seconds after `cancelled` or `finished`.
- Dashboard rendering that grows and shrinks as timers come and go, with smooth countdown animation driven by `started_at` / `finishes_at` attributes.
- All existing voice intents continue to work unchanged: `HassStartTimer`, `HassPauseTimer`, `HassUnpauseTimer`, `HassCancelTimer`, `HassIncreaseTimer`, `HassDecreaseTimer`, `HassTimerStatus`.
- Per-timer control services so the dashboard can offer tap-to-pause, swipe-to-add-5-minutes, and cancel buttons.
- A summary sensor (`sensor.voice_timers_active`) showing the count of running timers, for conditional dashboard sections and announcements.
- HACS-installable from day one.

### Out of scope

- Persistence across Home Assistant restarts. `TimerManager` is in-memory only; when HA restarts, every timer (voice or otherwise) dies. The integration follows the same lifecycle. Replicating timers from a custom store would create a divergence from the voice path and a worse bug surface than the loss avoids.
- Wake-word-free voice control or any STT/TTS work. Voice path is unchanged.
- Replacement of any existing voice intent. The integration is read-side instrumentation plus a thin control layer.

## Why wrap, not replace

`TimerManager` exposes `register_handler(device_id, handler)`, which replaces any existing handler for that device. Calling it from this integration would kick out Voice PE's handler and break the LED ring. There is no public "add additional listener" hook.

Options considered:

- **Wrap each registered handler.** The original handler is still called (LED ring still counts down, native chime still plays), and additionally an HA event is fired. This is what the integration does.
- **Replace the handler and re-implement device behaviour.** Much more work, brittle, and breaks every non-Voice-PE satellite differently.
- **Submit a core PR adding an observer API or a sensor platform inside `intent`.** Architecturally correct and the long-term direction, but weeks-to-months of review and design discussion. Not a substitute for shipping today. Worth opening once this integration is stable enough to demonstrate the use case.

The wrapping approach has one fragile spot: it reaches into `TimerManager.handlers` (a public attribute today, with `_handlers` as a fallback). If core renames it, setup fails closed with a clear log message, the integration shows red in the UI, and the voice path is untouched. `async_unload_entry` restores the original handlers cleanly, so a broken update is one click to roll back.

## Architecture

```
Voice command
   │
   ▼
Assist pipeline ──► HassStartTimer / HassIncreaseTimer / …
   │
   ▼
TimerManager ──► dispatches event to handlers[device_id]
                            │
                            ▼
                  wrapped handler (this integration)
                     │            │
                     ▼            ▼
            original handler   bus.async_fire("voice_timers_event", …)
            (LED ring, chime)                  │
                                               ▼
                                        sensor platform
                                               │
                                               ▼
                                  add / update / remove entity
                                               │
                                               ▼
                                  auto-entities + timer-bar-card
```

On setup, the integration enumerates handlers already registered (handlers for satellites that booted before this integration) and wraps each. It also monkey-patches `TimerManager.register_handler` so any satellite registering after setup is wrapped automatically (e.g. a Voice PE that comes online late, or a new browser satellite).

On unload, originals are restored and the monkey-patch is reverted.

## Repo layout

```
voice-timers/                          ← GitHub repo root
├── README.md                          ← user-facing install + dashboard guide
├── SPEC.md                            ← this file
├── hacs.json                          ← HACS metadata
├── info.md                            ← shown inside HACS store
├── LICENSE                            ← MIT or Apache 2.0
└── custom_components/
    └── voice_timers/
        ├── __init__.py                ← setup, handler wrapping, services
        ├── manifest.json
        ├── const.py
        ├── config_flow.py             ← single-instance flow, no options
        ├── sensor.py                  ← per-timer sensors + summary sensor
        ├── services.yaml              ← service schemas for pause/cancel/extend
        └── strings.json               ← translation strings
```

## File responsibilities

### `__init__.py`

- Locate the `TimerManager` instance in `hass.data` by isinstance scan.
- Wrap every existing handler in `handlers` (fallback `_handlers`).
- Monkey-patch `register_handler` so future registrations are wrapped.
- Fire `voice_timers_event` on every lifecycle callback with full `TimerInfo` payload.
- Register four services on the `voice_timers` domain: `pause`, `unpause`, `cancel`, `add_time`. Each takes `timer_id` (and `seconds` for `add_time`, which accepts negatives for shrinking). Services call the corresponding `TimerManager` methods that the built-in intent handlers use; the implementer should read `homeassistant.components.intent.timers` to confirm the exact method names (likely `pause_timer`, `unpause_timer`, `cancel_timer`, `add_time_to_timer`).
- On unload: restore originals, revert the monkey-patch, drop `hass.data[DOMAIN]`.

### `sensor.py`

Two entity classes:

- **`VoiceTimerSensor`**: one instance per active timer, keyed by `timer_id`. State is `active` / `paused` / `finished` / `cancelled`. Attributes: `timer_id`, `device_id`, `friendly_label`, `duration` (HH:MM:SS), `remaining` (HH:MM:SS), `started_at` (ISO), `finishes_at` (ISO), `seconds_total`, `seconds_left`. `started_at`/`finishes_at` are the contract with timer-bar-card.
- **`VoiceTimersActiveSensor`**: a singleton summary. State is the integer count of active timers. Attributes: `timer_ids` (list), `by_device` (dict of device_id → count).

Both subscribe to `voice_timers_event`. The summary sensor updates on every event; per-timer sensors update on `updated` payloads matching their `timer_id`.

Removal: 30-second linger after `cancelled` or `finished` to let the user see the final state, then `async_remove(force_remove=True)`.

### `services.yaml`

```yaml
pause:
  name: Pause timer
  description: Pause an active voice timer.
  fields:
    timer_id:
      name: Timer ID
      required: true
      selector:
        text:

unpause:
  name: Unpause timer
  description: Resume a paused voice timer.
  fields:
    timer_id:
      required: true
      selector:
        text:

cancel:
  name: Cancel timer
  description: Cancel a voice timer without firing the finished event.
  fields:
    timer_id:
      required: true
      selector:
        text:

add_time:
  name: Add time
  description: Extend (or shrink, with a negative value) a running timer.
  fields:
    timer_id:
      required: true
      selector:
        text:
    seconds:
      required: true
      selector:
        number:
          min: -3600
          max: 3600
          unit_of_measurement: s
```

### `manifest.json`

```json
{
  "domain": "voice_timers",
  "name": "Voice Timers",
  "codeowners": ["@fraserg"],
  "config_flow": true,
  "dependencies": ["intent"],
  "documentation": "https://github.com/fraserg/voice-timers",
  "iot_class": "local_push",
  "issue_tracker": "https://github.com/fraserg/voice-timers/issues",
  "version": "0.1.0"
}
```

### `hacs.json`

```json
{
  "name": "Voice Timers",
  "render_readme": true,
  "homeassistant": "2024.6.0"
}
```

The minimum HA version should be set to whichever release first stabilised `TimerManager` and `TimerEventType` in their current shape. `2024.6.0` is a safe floor; bump it if testing reveals breakage on earlier releases.

## Dashboard wiring

Requires `auto-entities` and `timer-bar-card` from HACS. Example card:

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

Conditional section using the summary sensor:

```yaml
type: conditional
conditions:
  - entity: sensor.voice_timers_active
    state_not: "0"
card:
  # …auto-entities card from above
```

## Acceptance criteria

The integration is complete when all of the following hold on a clean install:

1. Saying "set a 5 minute pasta timer" on a Voice PE creates `sensor.voice_timer_<uuid>` with `friendly_label: pasta`, state `active`, and `finishes_at` ~5 minutes ahead.
2. Saying "set a 30 minute laundry timer" on the same device creates a second sensor without disturbing the first. Both appear on the dashboard side by side.
3. Saying "add 2 minutes to the pasta timer" updates the existing sensor's `finishes_at` and `seconds_left` without creating a new one. The LED ring on Voice PE continues to count down accurately.
4. Saying "pause the pasta timer" changes the sensor state to `paused` and keeps `seconds_left` frozen.
5. Saying "cancel the pasta timer" or letting it finish removes the sensor 30 seconds later. The dashboard card shrinks.
6. Calling `voice_timers.add_time` with `timer_id` and `seconds: 60` from the developer tools extends a timer by 1 minute and the LED ring reflects the change (i.e. the service calls into `TimerManager` rather than independently mutating the sensor).
7. `sensor.voice_timers_active` reports the integer count of active timers and updates on every lifecycle event.
8. Reloading the integration via the UI restores original handlers (verifiable by checking that voice timers still work but no `voice_timers_event` fires).
9. Stopping and starting Home Assistant: existing voice timers are lost (this is expected; `TimerManager` is in-memory). Voice timer creation works immediately after restart without needing to reload the integration.
10. HACS installation from the repo URL works and adds the integration to the store with `info.md` rendered.

## Test plan

Unit tests live in `tests/` and use the standard `pytest-homeassistant-custom-component` harness:

- **Handler wrapping**: build a stub `TimerManager`, register a stub handler, set the integration up, assert the handler in the dict is now wrapped, fire a synthetic `started` event, assert both the stub handler was called and the HA event bus saw `voice_timers_event`.
- **Unload reversibility**: after unload, assert the dict contains the original stub handler and `register_handler` is no longer patched.
- **Late registration**: set up the integration first, then call `register_handler`, assert the freshly registered handler is wrapped.
- **Sensor lifecycle**: fire `started`, assert one sensor created with correct attributes; fire `updated`, assert no new sensor and `finishes_at` advanced; fire `finished`, assert state goes to `finished` and the entity is removed after 30 s (use `async_fire_time_changed`).
- **Multiple concurrent timers**: fire two `started` events with different `timer_id`s, assert two sensors and the summary sensor reports `2`.
- **Service calls**: call `voice_timers.pause` with a valid `timer_id`, assert the appropriate `TimerManager` method was invoked.

Manual smoke test on a real Voice PE before each release. Document the result in the PR.

## Known risks

- **`TimerManager` internals**: `handlers` is a public attribute but not part of the documented API. Core could rename or refactor. Mitigation: fallback to `_handlers`, fail closed on missing attribute with a clear log message, restore originals on unload.
- **TimerManager method names**: services call into internal methods that the built-in intent handlers also use. Names are stable across recent releases but not documented as public API. Mitigation: import the intent handler classes and inspect their implementation if names break, then patch.
- **Race on early load**: if `voice_timers` loads before `intent` has finished setup, `TimerManager` may not yet be in `hass.data`. Mitigation: `dependencies: ["intent"]` in manifest forces order. If that proves insufficient, retry once with a 1 s delay before failing.
- **Forgotten timers on restart**: documented in the README. Not a bug.
- **Browser satellites and Voice PE behaving differently**: every satellite registers its own handler with its own quirks. The integration only observes; it shouldn't introduce divergence, but worth testing each satellite type that the user has running.

## Future work

Once stable, open an upstream issue or draft PR proposing one of:

- A public `TimerManager.async_register_observer(callback)` method that allows multiple subscribers (additive, doesn't break the current per-device handler model).
- A sensor platform inside `homeassistant.components.intent` that ships this functionality in core.

The custom integration becomes redundant once either lands and can be archived. Until then, this is the only practical path.

## References

- TimerManager source: [homeassistant/components/intent/timers.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/intent/timers.py)
- Intent integration entry point: [homeassistant/components/intent/__init__.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/intent/__init__.py)
- Feature request being closed by this work: [esphome/home-assistant-voice-pe#387](https://github.com/esphome/home-assistant-voice-pe/issues/387)
- timer-bar-card: [rianadon/timer-bar-card](https://github.com/rianadon/timer-bar-card)
- auto-entities: [thomasloven/lovelace-auto-entities](https://github.com/thomasloven/lovelace-auto-entities)

## Starter code

A v0 implementation already exists with the wrapping logic, the per-timer sensor, the config flow, and the manifest. It does not yet include the services, the summary sensor, or the HACS files. Drop those v0 files into `custom_components/voice_timers/` and extend per this spec.
