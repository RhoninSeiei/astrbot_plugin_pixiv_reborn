from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from ..utils.pixiv_ids import parse_pixiv_user_id
from ..utils.database import (
    add_subscription,
    remove_subscription,
    list_subscriptions,
)


class SubscribeHandler:
    """
    Pixiv 订阅功能处理器
    负责处理画师订阅的添加、删除和查看功能
    """

    def __init__(self, client_wrapper, pixiv_config):
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client_api
        self.pixiv_config = pixiv_config

    def _matching_artist_ids(self, chat_id, artist_id):
        return list(dict.fromkeys(
            sub.target_id for sub in list_subscriptions(chat_id)
            if sub.sub_type == "artist"
            and parse_pixiv_user_id(str(sub.target_id)) == artist_id
        ))

    async def pixiv_subscribe_add(self, event: AstrMessageEvent, artist_id: str = ""):
        """订阅画师"""
        if not self.pixiv_config.subscription_enabled:
            yield event.plain_result("订阅功能未启用。")
            return

        artist_id = parse_pixiv_user_id(artist_id)
        if not artist_id:
            yield event.plain_result(
                "请输入有效的画师ID或 Pixiv 画师链接。"
                "用法: /pixiv_subscribe_add <画师ID或链接>"
            )
            return

        chat_id = event.get_group_id() or event.get_sender_id()
        if self._matching_artist_ids(chat_id, artist_id):
            yield event.plain_result(f"当前会话已经订阅画师 {artist_id}。")
            return

        platform_name = event.platform_meta.id
        message_type = event.get_message_type().value
        session_id = f"{platform_name}:{message_type}:{event.get_group_id() or event.get_sender_id()}"

        sub_type = "artist"
        target_name = artist_id
        latest_illust_id = 0

        try:
            if not await self.client_wrapper.authenticate():
                yield event.plain_result(self.pixiv_config.get_auth_error_message())
                return

            # 获取画师信息
            user_detail = await self.client_wrapper.call_pixiv_api(
                self.client.user_detail, int(artist_id)
            )
            if (
                not user_detail
                or getattr(user_detail, "error", None)
                or not getattr(user_detail, "user", None)
                or not getattr(user_detail.user, "name", None)
            ):
                raise ValueError("画师信息查询未成功")
            target_name = user_detail.user.name

            # 获取画师最新作品ID作为初始值
            user_illusts = await self.client_wrapper.call_pixiv_api(
                self.client.user_illusts, int(artist_id)
            )
            if (
                user_illusts is None
                or getattr(user_illusts, "error", None)
                or not isinstance(getattr(user_illusts, "illusts", None), list)
            ):
                raise ValueError("最新作品查询未成功")
            if user_illusts.illusts:
                latest_illust_id = int(user_illusts.illusts[0].id)
                if latest_illust_id <= 0:
                    raise ValueError("最新作品ID无效")
                logger.info(
                    f"获取到画师 {artist_id} 的最新作品ID: {latest_illust_id}"
                )

        except Exception as e:
            logger.error(f"获取画师 {artist_id} 信息失败: {e}")
            yield event.plain_result(
                f"无法获取画师 {artist_id} 的信息或最新作品，未添加订阅。请稍后重试。"
            )
            return

        success, message = add_subscription(
            event.get_group_id() or event.get_sender_id(),
            session_id,
            sub_type,
            artist_id,
            target_name,
            latest_illust_id,
        )
        yield event.plain_result(message)

    async def pixiv_subscribe_remove(
        self, event: AstrMessageEvent, artist_id: str = ""
    ):
        """取消订阅画师"""
        if not self.pixiv_config.subscription_enabled:
            yield event.plain_result("订阅功能未启用。")
            return

        artist_id = parse_pixiv_user_id(artist_id)
        if not artist_id:
            yield event.plain_result(
                "请输入有效的画师ID或 Pixiv 画师链接。"
                "用法: /pixiv_subscribe_remove <画师ID或链接>"
            )
            return

        chat_id = event.get_group_id() or event.get_sender_id()
        sub_type = "artist"

        targets = self._matching_artist_ids(chat_id, artist_id) or [artist_id]
        messages = []
        for target in targets:
            success, message = remove_subscription(chat_id, sub_type, target)
            messages.append(message)
        yield event.plain_result("\n".join(dict.fromkeys(messages)))

    async def pixiv_subscribe_list(self, event: AstrMessageEvent, args: str = ""):
        """查看当前订阅列表"""
        if not self.pixiv_config.subscription_enabled:
            yield event.plain_result("订阅功能未启用。")
            return

        chat_id = event.get_group_id() or event.get_sender_id()
        subs = list_subscriptions(chat_id)

        if not subs:
            yield event.plain_result("您还没有任何订阅。")
            return

        msg = "您的订阅列表：\n"
        for sub in subs:
            msg += f"- [画师] {sub.target_name} ({sub.target_id})\n"
        yield event.plain_result(msg)
