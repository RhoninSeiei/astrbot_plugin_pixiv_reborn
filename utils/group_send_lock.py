import asyncio


_GROUP_SEND_LOCKS: dict[str, asyncio.Lock] = {}


def get_group_send_lock(chat_id: str) -> asyncio.Lock:
    key = str(chat_id)
    lock = _GROUP_SEND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GROUP_SEND_LOCKS[key] = lock
    return lock


def get_group_send_lock_registry() -> dict[str, asyncio.Lock]:
    return _GROUP_SEND_LOCKS
