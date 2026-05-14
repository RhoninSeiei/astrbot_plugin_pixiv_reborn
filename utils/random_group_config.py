from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RandomSearchRuntimeConfig:
    return_count: int
    min_likes: Optional[int]
    min_interval_minutes: int
    max_interval_minutes: int


def _optional_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_or_default(value, default: int) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed < 1:
        parsed = _optional_int(default)
    if parsed is None or parsed < 1:
        return 1
    return parsed


def _optional_non_negative(value):
    parsed = _optional_int(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def _get_attr(source, name, default=None):
    if source is None:
        return default
    return getattr(source, name, default)


def resolve_random_search_runtime_config(
    base_config, group_config=None
) -> RandomSearchRuntimeConfig:
    return_count = _positive_or_default(
        _get_attr(group_config, "return_count"),
        _get_attr(base_config, "return_count", 1),
    )

    min_likes = None
    if group_config is not None:
        min_likes = _optional_non_negative(_get_attr(group_config, "min_likes"))

    min_interval = _positive_or_default(
        _get_attr(group_config, "min_interval_minutes"),
        _get_attr(base_config, "random_search_min_interval", 60),
    )
    max_interval = _positive_or_default(
        _get_attr(group_config, "max_interval_minutes"),
        _get_attr(base_config, "random_search_max_interval", 120),
    )
    if max_interval < min_interval:
        max_interval = min_interval

    return RandomSearchRuntimeConfig(
        return_count=return_count,
        min_likes=min_likes,
        min_interval_minutes=min_interval,
        max_interval_minutes=max_interval,
    )
