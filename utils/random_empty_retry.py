import random


DEFAULT_RETRY_DEPTH_CAP = 10


def _coerce_non_negative_int(value, default=0):
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def resolve_retry_depth(base_depth, extra_depth, depth_cap=DEFAULT_RETRY_DEPTH_CAP):
    extra = _coerce_non_negative_int(extra_depth)
    if extra <= 0:
        return base_depth

    if isinstance(base_depth, bool):
        return base_depth
    try:
        base = int(base_depth)
    except (TypeError, ValueError):
        return base_depth

    if base <= 0:
        return base

    cap = _coerce_non_negative_int(depth_cap, DEFAULT_RETRY_DEPTH_CAP)
    if cap <= 0:
        return base + extra
    if base >= cap:
        return base
    return min(base + extra, cap)


def build_retry_source_sequence(
    options,
    selected,
    retry_sources=0,
    shuffle_func=random.shuffle,
):
    if not options:
        return []

    extra_count = _coerce_non_negative_int(retry_sources)
    remaining = [option for option in options if option != selected]
    shuffle_func(remaining)
    return [selected] + remaining[:extra_count]
