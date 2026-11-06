from functools import wraps


def is_bot_admin(event):
    checker = getattr(event, "is_admin", None)
    return bool(callable(checker) and checker())


async def can_manage_chat(event):
    if is_bot_admin(event):
        return True
    if not event.get_group_id():
        return True
    try:
        group = await event.get_group()
        sender = str(event.get_sender_id())
        return bool(group) and (
            sender == str(group.group_owner)
            or sender in {str(value) for value in group.group_admins}
        )
    except Exception:
        return False


def require_management(func):
    @wraps(func)
    async def guarded(self, event, *args, **kwargs):
        if not await can_manage_chat(event):
            yield event.plain_result("仅机器人管理员、群主或群管理员可以管理本群 Pixiv 推送。")
            return
        async for result in func(self, event, *args, **kwargs):
            yield result
    return guarded


def resolve_content_mode(configured_mode, requested_filter):
    configured_mode = configured_mode or "过滤 R18"
    requested_mode = "仅 R18" if requested_filter == "r18" else "过滤 R18"
    if configured_mode not in ("允许 R18", requested_mode):
        raise ValueError("本次请求与当前 Pixiv 内容过滤设置不符。")
    return requested_mode
