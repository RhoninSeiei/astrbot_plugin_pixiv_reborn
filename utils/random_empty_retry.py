import random


DEFAULT_RETRY_DEPTH_CAP = 30


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


def enforce_random_push_delivery_policy(filter_config_kwargs):
    """Keep scheduled random pushes as direct chat messages."""
    normalized = dict(filter_config_kwargs)
    normalized["show_filter_result"] = False
    normalized["single_response_mode"] = False
    normalized["forward_threshold"] = False
    return normalized


def is_random_push_image_failure_notice(message_content):
    """Detect fallback text that should not be sent by scheduled random pushes."""
    failure_markers = (
        "图片下载失败",
        "动图处理失败",
        "处理动图时发生错误",
    )
    texts = []

    chain = getattr(message_content, "chain", None)
    if isinstance(chain, (list, tuple)):
        for item in chain:
            for attr in ("text", "message", "content"):
                value = getattr(item, attr, None)
                if isinstance(value, str):
                    texts.append(value)
            texts.append(str(item))
    else:
        texts.append(str(message_content))

    return any(marker in text for text in texts for marker in failure_markers)


def is_send_timeout_after_accept(error) -> bool:
    """判断 QQ sendMsg 是否在服务端受理后等待消息更新回执超时。"""
    text_parts = [repr(error), str(error)]
    for attr in ("retcode", "message", "wording", "status"):
        value = getattr(error, attr, None)
        if value is not None:
            text_parts.append(str(value))
    text = "\n".join(text_parts).replace('\\"', '"')
    compact_text = "".join(text.split())

    has_timeout_retcode = (
        getattr(error, "retcode", None) == 1200
        or "retcode=1200" in text
        or "retcode': 1200" in text
        or '"retcode": 1200' in text
    )
    has_send_timeout = "Timeout:" in text and "sendMsg" in text
    has_success_event = '"result":0' in compact_text and '"errMsg":""' in compact_text
    return has_timeout_retcode and has_send_timeout and has_success_event
