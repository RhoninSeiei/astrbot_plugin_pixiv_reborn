from datetime import datetime, time, timedelta


def parse_time_of_day(value: str) -> time:
    """Parse HH:MM time strings used by random search quiet hours."""
    raw = str(value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid time value: {value!r}")

    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"invalid time value: {value!r}")

    return time(hour=hour, minute=minute)


def is_in_quiet_hours(
    current: datetime,
    quiet_start: str,
    quiet_end: str,
    *,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False

    start = parse_time_of_day(quiet_start)
    end = parse_time_of_day(quiet_end)
    if start == end:
        return False

    current_time = current.time()
    if start < end:
        return start <= current_time < end

    return current_time >= start or current_time < end


def normalize_schedule_time(
    candidate: datetime,
    quiet_start: str,
    quiet_end: str,
    *,
    enabled: bool = True,
) -> datetime:
    try:
        if not is_in_quiet_hours(
            candidate, quiet_start, quiet_end, enabled=enabled
        ):
            return candidate

        end = parse_time_of_day(quiet_end)
    except (TypeError, ValueError):
        return candidate

    scheduled = candidate.replace(
        hour=end.hour,
        minute=end.minute,
        second=0,
        microsecond=0,
    )
    if scheduled <= candidate:
        scheduled += timedelta(days=1)
    return scheduled
