"""A pre-dispatch rule; timestamps may use different UTC offsets."""

from datetime import datetime


HEARTBEAT_AGE_THRESHOLD_MINUTES = 15


def heartbeat_age_minutes(dispatched_at: str, last_heartbeat_at: str) -> float:
    sent = datetime.fromisoformat(dispatched_at.replace("Z", "+00:00"))
    heartbeat = datetime.fromisoformat(last_heartbeat_at.replace("Z", "+00:00"))
    if sent.tzinfo is None or heartbeat.tzinfo is None:
        raise ValueError("Both timestamps must include UTC offsets")
    age = (sent - heartbeat).total_seconds() / 60
    if age < 0:
        raise ValueError("Heartbeat occurs after dispatch")
    return age


def should_probe(dispatched_at: str, last_heartbeat_at: str) -> bool:
    """Request a preflight reachability check before dispatch if stale."""
    return heartbeat_age_minutes(dispatched_at, last_heartbeat_at) >= HEARTBEAT_AGE_THRESHOLD_MINUTES
