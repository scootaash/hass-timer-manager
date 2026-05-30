"""Constants for the Voice Timers integration."""

DOMAIN = "voice_timers"
EVENT_TIMER = f"{DOMAIN}_event"
LINGER_SECONDS = 30           # cancelled timers: vanish quickly
LINGER_SECONDS_FINISHED = 3600  # finished timers: visible for 1 hour
