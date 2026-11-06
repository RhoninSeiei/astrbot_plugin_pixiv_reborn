"""Resolve a single bot image from a server-verified OneBot group reply."""

import asyncio
import re
from contextlib import aclosing
from urllib.parse import parse_qs, urlsplit

from astrbot.api import logger
from astrbot.api.message_components import Reply

from ..utils.delivery_guard import delivery_status, guarded_send
from ..utils.group_send_lock import get_group_send_lock
from ..utils.pixiv_utils import (
    cleanup_pixiv_temp_files,
    send_forward_message,
    send_pixiv_image,
)
from ..utils.random_empty_retry import is_random_push_image_failure_notice
from ..utils.tag import (
    FilterConfig,
    build_detail_message,
    filter_illusts_with_reason,
    process_and_send_illusts,
)


class QuoteError(ValueError):
    pass


SELECT_ONE = (
    "引用中包含多个作品或转发、嵌套引用，无法确定目标。"
    "请引用机器人发送的单个作品图片；合并转发请先打开并定位原始单作品消息，"
    "或用 /pixiv_specific <作品ID> 获取单作品后再引用。"
)


def _integer(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if not re.fullmatch(r"-?[0-9]{1,20}" if signed else r"[0-9]{1,20}", str(value)):
        return None
    number = int(value)
    return number if (number != 0 if signed else number > 0) else None


def has_quote(event):
    return any(isinstance(segment, Reply) for segment in event.get_messages())


def _parse_message(message):
    if not isinstance(message, list):
        raise QuoteError("无法验证引用消息的图片内容，请引用本机器人发送的单个作品图片。")
    texts = []
    has_image = False
    for segment in message:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            raise QuoteError("引用消息格式不完整，无法验证作品。")
        kind, data = segment.get("type"), segment["data"]
        if kind in {"reply", "forward", "node"}:
            raise QuoteError(SELECT_ONE)
        if kind == "image":
            has_image |= any(isinstance(data.get(key), str) and bool(data[key].strip())
                             for key in ("file", "url"))
        elif kind == "text":
            if not isinstance(data.get("text"), str):
                raise QuoteError("引用消息文本格式不完整，无法验证作品。")
            texts.append(data["text"])
    ids = set()
    # Concatenate adjacent text segments before matching complete generated lines.
    for line in "".join(texts).splitlines():
        match = re.fullmatch(r"作品ID: ([1-9][0-9]{0,19})", line)
        if not match:
            match = re.fullmatch(
                r"(?:(?:链接|作品链接): )?https://www\.pixiv\.net/artworks/([1-9][0-9]{0,19})",
                line,
            )
        if match:
            ids.add(int(match.group(1)))
    if len(ids) > 1:
        raise QuoteError(SELECT_ONE)
    if not has_image or not ids:
        raise QuoteError(
            "引用消息中没有可验证的 Pixiv 图片和作品ID或官方作品链接。"
            "请引用本机器人发送的单个作品图片；旧版隐藏详情图片可能没有作品标识。"
        )
    return ids.pop()


async def recover_quoted_illust_id(event):
    if event.get_platform_name() != "aiocqhttp" or not event.get_group_id():
        raise QuoteError("引用图片操作目前仅支持能通过 get_msg 验证原消息的 OneBot 群聊。")
    replies = [segment for segment in event.get_messages() if isinstance(segment, Reply)]
    if len(replies) != 1:
        raise QuoteError("请引用本机器人发送的一条单作品图片消息后使用此命令。")
    reply_id = _integer(replies[0].id, signed=True)
    group_id = _integer(event.get_group_id())
    self_id = _integer(event.get_self_id())
    if reply_id is None or group_id is None or self_id is None:
        raise QuoteError("无法验证引用消息的标识或当前群聊。")
    try:
        # Same public adapter API used by AstrBot's own reply conversion.
        result = await asyncio.wait_for(
            event.bot.call_action(action="get_msg", message_id=reply_id), timeout=15
        )
    except Exception:
        logger.warning("Pixiv 引用原消息读取失败。")
        raise QuoteError("无法读取引用的原消息，请确认消息未撤回且 OneBot 支持 get_msg。") from None
    if not isinstance(result, dict):
        raise QuoteError("引用原消息返回格式无效，未执行操作。")
    sender = result.get("sender")
    if (
        _integer(result.get("message_id"), signed=True) != reply_id
        or result.get("message_type") != "group"
        or _integer(result.get("group_id")) != group_id
        or not isinstance(sender, dict)
        or _integer(sender.get("user_id")) != self_id
    ):
        raise QuoteError("引用原消息必须由当前机器人发送，且属于当前群聊并与引用ID一致。")
    return _parse_message(result.get("message"))


def _contains_image(content, depth=0):
    if depth > 6:
        return False
    if str(getattr(content, "type", "")).rsplit(".", 1)[-1].lower() == "image":
        return True
    for key in ("chain", "nodes", "content"):
        children = getattr(content, key, None)
        if isinstance(children, (list, tuple)) and any(
            _contains_image(child, depth + 1) for child in children
        ):
            return True
    return False


def _contains_failure(content, depth=0):
    if depth > 6:
        return True
    if is_random_push_image_failure_notice(str(getattr(content, "text", ""))):
        return True
    for key in ("chain", "nodes", "content"):
        children = getattr(content, key, None)
        if isinstance(children, (list, tuple)) and any(
            _contains_failure(child, depth + 1) for child in children
        ):
            return True
    return False


class QuoteHandler:
    def __init__(self, client_wrapper, pixiv_config):
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client_api
        self.pixiv_config = pixiv_config

    async def resolve(self, event):
        illust_id = await recover_quoted_illust_id(event)
        if not await self.client_wrapper.authenticate():
            raise QuoteError(self.pixiv_config.get_auth_error_message())
        try:
            result = await self.client_wrapper.call_pixiv_api(self.client.illust_detail, illust_id)
            illust = getattr(result, "illust", None)
            if (
                _integer(getattr(illust, "id", None)) != illust_id
                or _integer(getattr(getattr(illust, "user", None), "id", None)) is None
            ):
                raise QuoteError("无法从 Pixiv 确认该作品及画师，未执行操作。")
            return illust
        except QuoteError:
            raise
        except Exception:
            logger.warning("Pixiv 引用作品详情查询失败。")
            raise QuoteError("Pixiv 作品详情查询失败，未执行操作，请稍后重试。") from None

    def _filter_config(self, count):
        config = self.pixiv_config
        return FilterConfig(
            r18_mode=config.r18_mode,
            filter_r18g_only=config.filter_r18g_only,
            ai_filter_mode=config.ai_filter_mode,
            ai_detection_mode=config.ai_detection_mode,
            return_count=count,
            display_tag_str="引用作品的同画师作品",
            excluded_tags=list(config.automatic_push_excluded_tags),
            show_filter_result=config.show_filter_result,
            single_response_mode=config.single_response_mode,
            forward_threshold=config.forward_threshold,
            show_details=config.show_details,
            min_bookmarks=config.min_bookmarks,
            min_views=config.min_views,
            min_likes=config.min_likes,
        )

    async def _exclude_unavailable(self, event, items):
        from ..utils.database import SentIllust

        if not items:
            return []
        ids = [item.id for item in items]

        def sent_ids():
            # Unlike legacy convenience filters, a failed lookup must not fail open.
            return {row.illust_id for row in SentIllust.select(SentIllust.illust_id).where(
                (SentIllust.chat_id == str(event.get_group_id()))
                & SentIllust.illust_id.in_(ids)
            )}

        old_sent = await asyncio.to_thread(sent_ids)
        states = await delivery_status(str(event.unified_msg_origin), ids)
        blocked = old_sent | {
            key for key, row in states.items() if row["state"] != "failed"
        }
        return [item for item in items if item.id not in blocked]

    async def _candidates(self, event, work, filter_config):
        candidates, seen = [], {int(work.id)}
        offset = None
        depth = max(1, min(int(self.pixiv_config.deep_search_depth), 5))
        for _ in range(depth):
            kwargs = {} if offset is None else {"offset": offset}
            response = await self.client_wrapper.call_pixiv_api(
                self.client.user_illusts, int(work.user.id), **kwargs
            )
            items = getattr(response, "illusts", None)
            if not isinstance(items, list):
                raise QuoteError("画师作品查询失败，未发送图片。")
            page = []
            for item in items:
                item_id = _integer(getattr(item, "id", None))
                if (
                    item_id is not None and item_id not in seen
                    and _integer(getattr(getattr(item, "user", None), "id", None)) == int(work.user.id)
                ):
                    seen.add(item_id)
                    page.append(item)
            page = await self._exclude_unavailable(event, page)
            filtered, _ = filter_illusts_with_reason(page, filter_config)
            candidates.extend(filtered)
            if len(candidates) >= filter_config.return_count:
                break
            next_url = getattr(response, "next_url", None)
            if not isinstance(next_url, str) or not next_url:
                break
            offsets = parse_qs(urlsplit(next_url).query).get("offset", [])
            next_offset = _integer(offsets[0]) if len(offsets) == 1 else None
            if next_offset is None or next_offset <= (offset or 0):
                break
            offset = next_offset
        return candidates

    async def more(self, event, work, count=3):
        from ..utils.database import add_sent_illust

        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise QuoteError("作品数量必须为 1-10 的整数。")
        session = str(event.unified_msg_origin)
        if not session.strip():
            raise QuoteError("无法确定当前会话，未发送图片。")
        async with get_group_send_lock(str(event.get_group_id())):
            config = self._filter_config(count)
            candidates = await self._candidates(event, work, config)
            if not candidates:
                yield event.plain_result("没有找到可追加的同画师作品；已排除引用作品、已发送及待确认作品，并应用当前内容过滤。")
                return
            messages = process_and_send_illusts(
                candidates, config, self.client, event, build_detail_message,
                send_pixiv_image, send_forward_message, include_related_ids=True,
            )
            async with aclosing(messages):
                async for message, ids in messages:
                    if not ids:
                        yield message
                        continue
                    try:
                        if not _contains_image(message) or _contains_failure(message):
                            yield event.plain_result("图片准备失败，本条消息未发送，请稍后重试。")
                            continue
                        outcome = await guarded_send(session, ids, lambda: event.send(message), reject_sent=True)
                        if outcome.status == "sent":
                            for item_id in ids:
                                await asyncio.to_thread(add_sent_illust, item_id, str(event.get_group_id()))
                        elif outcome.status in {"unknown", "blocked"}:
                            if outcome.status == "blocked" and outcome.error is not None and not outcome.blocked_ids:
                                yield event.plain_result("发送记录无法写入，本条图片未开始发送；请检查存储后重试。")
                            else:
                                yield event.plain_result("作品已发送或发送结果待核实，本次停止追加，不会自动重发。待核实记录可由管理员使用 /pixiv 待核实 查看。")
                            return
                        else:
                            yield event.plain_result("本条图片发送失败，未计为送达。")
                    finally:
                        await cleanup_pixiv_temp_files(message)
