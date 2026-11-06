"""Parse explicit short commands without changing legacy tag searches."""

import re
from dataclasses import dataclass
from .pixiv_ids import parse_pixiv_user_id


CHAT_HELP = """Pixiv 聊天命令：
/pixiv 搜图 <标签> [N张]（N 为 1-10，仅本次生效）
/pixiv 订阅 <画师ID或主页链接>
/pixiv 取消订阅 <画师ID或主页链接>
/pixiv 订阅列表
/pixiv 暂停推送（当前会话的随机标签和随机排行榜）
/pixiv 恢复推送（当前会话的随机标签和随机排行榜）
/pixiv 推送状态（随机推送队列状态）
/pixiv 帮助
标签支持空格、逗号及 -排除标签，例如：
/pixiv 搜图 white dress,初音ミク,-ntr 3张
原有 /pixiv <标签> 和全部长命令继续可用。
完整帮助：/pixiv_help"""

_ALIASES = {
    "搜图": "search",
    "订阅": "subscribe_add",
    "取消订阅": "subscribe_remove",
    "订阅列表": "subscribe_list",
    "暂停推送": "random_suspend",
    "恢复推送": "random_resume",
    "推送状态": "random_status",
    "帮助": "help",
}


@dataclass(frozen=True)
class ChatCommand:
    action: str
    argument: str = ""
    count: int | None = None


def extract_pixiv_tail(message: str, fallback: str = "") -> str:
    """Recover the full tail; AstrBot's ordinary str parameter keeps one token."""
    # WakingCheckStage strips the configured wake prefix before command dispatch.
    match = re.fullmatch(r"\s*/?pixiv(?:\s+(.*?))?\s*", message, re.DOTALL)
    return (match.group(1) or "").strip() if match else fallback


def _artist_id(value: str) -> str:
    artist_id = parse_pixiv_user_id(value)
    if artist_id:
        return artist_id
    raise ValueError("请输入有效的画师ID或 Pixiv 画师主页链接。")


def parse_chat_command(tail: str) -> ChatCommand:
    parts = tail.strip().split(maxsplit=1)
    if not parts or parts[0] not in _ALIASES:
        return ChatCommand("legacy", tail)

    alias = parts[0]
    action = _ALIASES[alias]
    argument = parts[1].strip() if len(parts) == 2 else ""
    if action == "search":
        count = None
        last = argument.rsplit(maxsplit=1)
        # Only a numeric-looking suffix reserves the count syntax; 纸张 is a tag.
        if last and re.fullmatch(r"[+\-\d.]+张", last[-1]):
            number = last[-1][:-1]
            if not re.fullmatch(r"[0-9]{1,2}", number) or not 1 <= int(number) <= 10:
                raise ValueError("图片数量必须为 1-10 的整数。用法：/pixiv 搜图 <标签> [N张]")
            count = int(number)
            argument = last[0] if len(last) == 2 else ""
        if not argument:
            raise ValueError("请输入搜索标签。用法：/pixiv 搜图 <标签> [N张]")
        return ChatCommand(action, argument, count)

    if action in {"subscribe_add", "subscribe_remove"}:
        try:
            artist_id = _artist_id(argument)
        except ValueError as error:
            raise ValueError(
                f"{error} 用法：/pixiv {alias} <画师ID或主页链接>"
            ) from error
        return ChatCommand(action, artist_id)

    if argument:
        raise ValueError(f"此命令不接受额外参数。用法：/pixiv {alias}")
    return ChatCommand(action)
