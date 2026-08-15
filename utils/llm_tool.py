from typing import Any, List
import asyncio
import hashlib
import io
import base64
from pathlib import Path
from pydantic import Field
from pydantic.dataclasses import dataclass
from fpdf import FPDF

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.api import logger

from .tag import (
    build_detail_message,
    FilterConfig,
    filter_illusts_with_reason,
    validate_and_process_tags,
)
from .pixiv_utils import (
    send_pixiv_image,
    generate_safe_filename,
)
from .random_empty_retry import (
    is_random_push_image_failure_notice,
    is_send_timeout_after_accept,
)
from .group_send_lock import get_group_send_lock


async def _call_pixiv_api(pixiv_client_wrapper, func, *args, **kwargs):
    if pixiv_client_wrapper and hasattr(pixiv_client_wrapper, "call_pixiv_api"):
        return await pixiv_client_wrapper.call_pixiv_api(func, *args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)


def bind_tools_to_plugin_module(tools, module_path: str) -> None:
    """Bind dynamically created tools back to the plugin's main module."""
    if not module_path:
        return
    for tool in tools:
        object.__setattr__(tool, "__module__", module_path)
        tool.handler_module_path = module_path
        handler = getattr(tool, "handler", None)
        if handler is not None:
            handler.__module__ = module_path


PIXIV_TAG_ALIASES = [
    (("舰队收藏", "艦隊收藏", "舰队collection", "kancolle"), "艦隊これくしょん"),
    (("舰c", "艦c", "舰これ", "艦これ"), "艦これ"),
    (("亚特兰大", "亞特蘭大", "atlanta"), "Atlanta(艦隊これくしょん)"),
    (("时雨", "時雨", "shigure"), "時雨(艦隊これくしょん)"),
    (("响", "響", "hibiki"), "響(艦隊これくしょん)"),
    (("伊莉雅", "伊莉亚", "依莉雅", "illya"), "イリヤスフィール・フォン・アインツベルン"),
    (("伊蕾娜", "イレイナ", "elaina"), "イレイナ"),
    (("碧蓝航线", "碧藍航線", "azur lane"), "アズールレーン"),
    (("勒马兰", "勒馬蘭", "le malin"), "ル・マラン(アズールレーン)"),
    (("碧蓝档案", "蔚蓝档案", "ブルアカ", "blue archive"), "ブルーアーカイブ"),
    (("梦幻之星在线2", "夢幻之星在線2", "pso2"), "ファンタシースターオンライン2"),
    (("艾梅斯", "エイメス", "aimess"), "エイメス"),
]

PIXIV_WORK_TAGS = {
    "艦隊これくしょん",
    "艦これ",
    "アズールレーン",
    "ブルーアーカイブ",
    "ファンタシースターオンライン2",
}

@dataclass
class PixivIllustSearchTool(FunctionTool[AstrAgentContext]):
    """
    Pixiv插画搜索工具
    """

    pixiv_client: Any = None
    pixiv_config: Any = None
    pixiv_client_wrapper: Any = None
    name: str = "pixiv_search_illust"
    description: str = (
        "根据LLM提取的主搜索词、作品名、角色名、Pixiv tag 或 tag 候选列表搜索 Pixiv 插画，"
        "并在当前事件上下文中发送图片。工具内部会做 Pixiv tag 规范化、别名转换、"
        "常用日文标签补全、角色名与作品名组合搜索和失败降级。"
        "返回给 Agent 的文本只描述执行状态。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "LLM从请求中提取的主 Pixiv tag 或搜索词。优先只填写角色名或主 tag，"
                        "例如“机器人发点神崎蘭子的图”应填写“神崎蘭子”；不要附加请求句式。"
                    ),
                },
                "count": {
                    "type": "integer",
                    "description": "期望返回数量，默认 1，最大 5。",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1,
                },
                "filters": {
                    "type": "string",
                    "description": "过滤条件：safe 或 r18，默认 safe。",
                    "enum": ["safe", "r18"],
                    "default": "safe",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选的 Pixiv tag 候选列表。能够识别精确 tag 时应填写，"
                        "插件会依次校验和搜索。"
                    ),
                    "default": [],
                },
                "source_text": {
                    "type": "string",
                    "description": "可选的群聊原始请求文本，仅用于诊断和结果说明。",
                    "default": "",
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            query = str(kwargs.get("query", "") or "").strip()
            source_text = str(kwargs.get("source_text", "") or "").strip()
            count = self._normalize_count(kwargs.get("count", 1))
            filters = self._normalize_filters(kwargs.get("filters", "safe"))
            tags = self._normalize_tags(kwargs.get("tags"))
            logger.info(
                "ToolLoopAgentRunner 执行 pixiv_search_illust: "
                "query=%r, count=%s, filters=%s, tags=%s, source_text=%r"
                % (query, count, filters, tags, source_text)
            )

            if not self.pixiv_client:
                return "错误: Pixiv客户端未初始化"

            if (
                self.pixiv_client_wrapper
                and not await self.pixiv_client_wrapper.authenticate()
            ):
                if self.pixiv_config and hasattr(
                    self.pixiv_config, "get_auth_error_message"
                ):
                    return self.pixiv_config.get_auth_error_message()
                return "Pixiv API 认证失败，请检查配置中的凭据信息。"

            candidates = self._build_search_candidates(query, tags, source_text)
            if not candidates:
                return "未找到可用的 Pixiv 搜索词"

            return await self._search_illust(candidates, query, context, count, filters)

        except Exception as e:
            logger.error(f"Pixiv插画搜索失败: {e}", exc_info=True)
            return f"搜索失败: {str(e)}"

    def _normalize_count(self, value) -> int:
        try:
            return min(max(int(value), 1), 5)
        except (TypeError, ValueError):
            return 1

    def _normalize_filters(self, value) -> str:
        normalized = str(value or "safe").strip().lower()
        return "r18" if normalized == "r18" else "safe"

    def _normalize_tags(self, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(item).strip() for item in value]
        else:
            raw_items = []

        normalized = []
        for item in raw_items:
            if not item:
                continue
            normalized.append(self._normalize_alias_tag(item) or item)
        return self._dedupe(normalized)

    def _normalize_alias_tag(self, text: str) -> str | None:
        normalized_text = text.strip().lower()
        for aliases, tag in PIXIV_TAG_ALIASES:
            if normalized_text == tag.lower():
                return tag
            if any(normalized_text == alias.lower() for alias in aliases):
                return tag
        return None

    def _extract_alias_tags(self, text: str) -> list[str]:
        lowered = text.lower()
        tags = []
        for aliases, tag in PIXIV_TAG_ALIASES:
            if tag.lower() in lowered or any(alias.lower() in lowered for alias in aliases):
                tags.append(tag)
        return self._dedupe(tags)

    def _dedupe(self, values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            key = value.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(value.strip())
        return result

    def _build_search_candidates(
        self, query: str, tags: list[str] | None = None, source_text: str = ""
    ) -> list[dict[str, str]]:
        normalized_tags = self._normalize_tags(tags or [])
        alias_tags = self._extract_alias_tags(query)
        structured_query = " ".join(query.strip().split())
        loose_queries = self._dedupe([structured_query])

        known_tags = self._dedupe(normalized_tags + alias_tags)
        work_tags = [tag for tag in known_tags if tag in PIXIV_WORK_TAGS]
        character_tags = [tag for tag in known_tags if tag not in PIXIV_WORK_TAGS]
        inferred_tags = self._dedupe([structured_query, *structured_query.split()])

        exact_tags = self._dedupe(character_tags + inferred_tags + work_tags)
        candidates: list[dict[str, str]] = []

        for tag in exact_tags:
            candidates.append(
                {
                    "word": tag,
                    "search_target": "exact_match_for_tags",
                    "label": tag,
                }
            )

        for work_tag in work_tags:
            for character_tag in character_tags:
                candidates.append(
                    {
                        "word": f"{work_tag} {character_tag}",
                        "search_target": "partial_match_for_tags",
                        "label": f"{work_tag} + {character_tag}",
                    }
                )

        for tag in exact_tags:
            candidates.append(
                {
                    "word": tag,
                    "search_target": "partial_match_for_tags",
                    "label": tag,
                }
            )

        for loose_query in loose_queries:
            candidates.append(
                {
                    "word": loose_query,
                    "search_target": "partial_match_for_tags",
                    "label": loose_query,
                }
            )

        deduped = []
        seen = set()
        for candidate in candidates:
            key = (candidate["word"].lower(), candidate["search_target"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    async def _search_illust(
        self, candidates, query, context, count=1, filters: str = "safe"
    ):
        event = self._get_event(context)
        chat_id = self._get_group_chat_id(event)
        if not chat_id:
            return await self._search_illust_unlocked(
                candidates, query, context, count, filters
            )

        lock = self._get_group_lock(chat_id)
        async with lock:
            return await self._search_illust_unlocked(
                candidates, query, context, count, filters
            )

    async def _search_illust_unlocked(
        self, candidates, query, context, count=1, filters: str = "safe"
    ):
        """按候选 tag 逐级搜索插画。"""
        event = self._get_event(context)
        chat_id = self._get_group_chat_id(event)
        retention_days = self._sent_retention_days()
        initial_pages = 3
        max_pages = 10
        filter_config = self._build_filter_config(query, count, filters)

        for candidate in candidates:
            tag_result = validate_and_process_tags(candidate["word"])
            if not tag_result["success"]:
                logger.warning(
                    f"pixiv_search_illust 跳过无效候选 tag: {candidate['word']}"
                )
                continue

            search_word = tag_result["search_tags"]
            search_target = candidate["search_target"]
            all_illusts = []
            seen_illust_ids = set()
            page_count = 0
            next_params = None

            while page_count < max_pages:
                try:
                    if page_count == 0:
                        search_result = await _call_pixiv_api(
                            self.pixiv_client_wrapper,
                            self.pixiv_client.search_illust,
                            search_word,
                            search_target=search_target,
                            sort="popular_desc",
                            filter="for_ios",
                            req_auth=True,
                        )
                    else:
                        if not next_params:
                            break
                        search_result = await _call_pixiv_api(
                            self.pixiv_client_wrapper,
                            self.pixiv_client.search_illust, **next_params
                        )

                    if not search_result or not hasattr(search_result, "illusts"):
                        break

                    if search_result.illusts:
                        for illust in search_result.illusts:
                            illust_id = getattr(illust, "id", None)
                            if illust_id is not None and illust_id in seen_illust_ids:
                                continue
                            if illust_id is not None:
                                seen_illust_ids.add(illust_id)
                            all_illusts.append(illust)
                        page_count += 1
                    else:
                        break

                    if hasattr(search_result, "next_url") and search_result.next_url:
                        next_params = self.pixiv_client.parse_qs(search_result.next_url)
                    else:
                        break

                    if page_count >= initial_pages:
                        if not chat_id:
                            break
                        unsent, _ = await self._partition_cached_illusts(
                            all_illusts,
                            chat_id,
                            retention_days,
                        )
                        sendable_unsent, _ = filter_illusts_with_reason(
                            unsent,
                            filter_config,
                        )
                        if len(sendable_unsent) >= count:
                            break

                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.warning(
                        "pixiv_search_illust 候选 %r 搜索失败: %s"
                        % (search_word, e)
                    )
                    break

            if not all_illusts:
                logger.info(f"pixiv_search_illust 候选 {search_word!r} 未找到结果")
                continue

            sorted_illusts = sorted(
                all_illusts,
                key=lambda x: getattr(x, "total_bookmarks", 0),
                reverse=True,
            )
            selected_illusts = sorted_illusts
            if chat_id:
                unsent, recent = await self._partition_cached_illusts(
                    sorted_illusts,
                    chat_id,
                    retention_days,
                )
                sendable_unsent, _ = filter_illusts_with_reason(
                    unsent,
                    filter_config,
                )
                sendable_recent, _ = filter_illusts_with_reason(
                    recent,
                    filter_config,
                )
                if sendable_unsent:
                    selected_illusts = sendable_unsent
                elif sendable_recent:
                    selected_illusts = sendable_recent
                    logger.info(
                        "pixiv_search_illust 候选 %r 在 %s 天内均已发送，"
                        "按最早发送时间回退。",
                        search_word,
                        retention_days,
                    )
                else:
                    logger.info(
                        "pixiv_search_illust 候选 %r 经内容和发送缓存过滤后无可发送作品",
                        search_word,
                    )
                    continue
            else:
                selected_illusts, _ = filter_illusts_with_reason(
                    selected_illusts,
                    filter_config,
                )
                if not selected_illusts:
                    continue

            return await self._send_pixiv_result(
                event,
                selected_illusts,
                query,
                search_word,
                count,
                filters,
                chat_id=chat_id,
                filter_config=filter_config,
            )

        return "未找到可发送图片"

    async def _send_pixiv_result(
        self,
        event,
        items,
        query,
        tags,
        count=1,
        filters: str = "safe",
        chat_id: str | None = None,
        filter_config: FilterConfig | None = None,
    ):
        """发送按热度排序的结果，并只返回执行状态。"""
        logger.info(f"PixivIllustSearchTool: 准备发送 {count} 张图片")
        if not event or not hasattr(event, "send"):
            return "未找到当前事件上下文，无法发送图片"

        config = filter_config or self._build_filter_config(query, count, filters)

        filtered_items, _ = filter_illusts_with_reason(items, config)
        filtered_items = self._dedupe_illusts(filtered_items)
        if not filtered_items:
            return "未找到可发送图片"

        sent_count = 0
        for illust in filtered_items:
            if sent_count >= config.return_count:
                break
            try:
                detail_message = build_detail_message(illust, is_novel=False)
                async for result in send_pixiv_image(
                    self.pixiv_client,
                    event,
                    illust,
                    detail_message,
                    show_details=config.show_details,
                ):
                    if is_random_push_image_failure_notice(result):
                        logger.warning(
                            f"pixiv_search_illust 跳过图片下载失败结果: {getattr(illust, 'id', 'unknown')}"
                        )
                        continue
                    try:
                        await event.send(result)
                    except Exception as e:
                        if not is_send_timeout_after_accept(e):
                            raise
                        logger.warning(
                            "pixiv_search_illust 作品 %s 的发送回执超时，"
                            "服务端已经受理，按发送成功处理。",
                            getattr(illust, "id", "unknown"),
                        )
                    if chat_id:
                        await self._record_sent_illust(illust.id, chat_id)
                    sent_count += 1
                    break
            except Exception as e:
                logger.warning(
                    f"pixiv_search_illust 发送作品 {getattr(illust, 'id', 'unknown')} 失败: {e}"
                )
                continue

        if sent_count > 0:
            return f"已发送 {sent_count} 张图片"
        return "未找到可发送图片"

    def _build_filter_config(
        self,
        query: str,
        count: int,
        filters: str,
    ) -> FilterConfig:
        return FilterConfig(
            r18_mode="仅 R18" if filters == "r18" else "过滤 R18",
            filter_r18g_only=self.pixiv_config.filter_r18g_only
            if self.pixiv_config
            else False,
            ai_filter_mode=self.pixiv_config.ai_filter_mode
            if self.pixiv_config
            else "过滤 AI 作品",
            ai_detection_mode=self.pixiv_config.ai_detection_mode
            if self.pixiv_config
            else "field_or_tag",
            display_tag_str=f"搜索:{query}",
            return_count=count,
            logger=logger,
            show_filter_result=False,
            single_response_mode=self.pixiv_config.single_response_mode
            if self.pixiv_config
            else False,
            excluded_tags=[],
            forward_threshold=self.pixiv_config.forward_threshold
            if self.pixiv_config
            else False,
            show_details=self.pixiv_config.show_details if self.pixiv_config else True,
        )

    def _dedupe_illusts(self, illusts) -> list:
        result = []
        seen_ids = set()
        for illust in illusts:
            illust_id = getattr(illust, "id", None)
            if illust_id is not None and illust_id in seen_ids:
                continue
            if illust_id is not None:
                seen_ids.add(illust_id)
            result.append(illust)
        return result

    def _sent_retention_days(self) -> int:
        value = getattr(
            self.pixiv_config,
            "llm_tool_sent_illust_retention_days",
            45,
        )
        try:
            return min(max(int(value), 1), 365)
        except (TypeError, ValueError):
            return 45

    def _get_group_chat_id(self, event) -> str | None:
        if not event:
            return None
        try:
            group_id = event.get_group_id()
            if group_id:
                return str(group_id)
        except Exception:
            pass

        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            marker = ":GroupMessage:"
            if marker in umo:
                return umo.rsplit(marker, 1)[1] or None
        except Exception:
            pass
        return None

    def _get_group_lock(self, chat_id: str) -> asyncio.Lock:
        return get_group_send_lock(chat_id)

    async def _partition_cached_illusts(
        self,
        illusts,
        chat_id: str,
        retention_days: int,
    ) -> tuple[list, list]:
        from .database import partition_sent_illusts

        return await asyncio.to_thread(
            partition_sent_illusts,
            illusts,
            chat_id,
            retention_days,
        )

    async def _record_sent_illust(self, illust_id: int, chat_id: str) -> None:
        from .database import add_sent_illust

        await asyncio.to_thread(add_sent_illust, illust_id, chat_id)

    def _get_event(self, context):
        try:
            agent_context = context.context if hasattr(context, "context") else context
            if hasattr(context, "event") and context.event:
                return context.event
            elif hasattr(agent_context, "event") and agent_context.event:
                return agent_context.event
        except Exception:
            pass
        return None

    def _format_text_results(self, items, query, tags):
        result = "找到以下插画:\n"
        for i, item in enumerate(items[:5], 1):
            title = getattr(item, "title", "未知标题")
            result += f"{i}. {title} (ID: {item.id})\n"
        return result


@dataclass
class PixivNovelSearchTool(FunctionTool[AstrAgentContext]):
    """
    Pixiv小说搜索工具
    """

    pixiv_client: Any = None
    pixiv_config: Any = None
    pixiv_client_wrapper: Any = None

    name: str = "pixiv_search_novel"
    description: str = "Pixiv小说搜索工具。用于搜索Pixiv上的小说，或者通过ID直接下载小说。支持输入关键词或纯数字ID。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或小说ID（纯数字）。",
                },
                "filters": {
                    "type": "string",
                    "description": "过滤条件，如 'safe', 'r18' 等",
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            query = kwargs.get("query", "")
            logger.info(f"Pixiv小说搜索工具：搜索 '{query}'")

            if not self.pixiv_client:
                return "错误: Pixiv客户端未初始化"

            if (
                self.pixiv_client_wrapper
                and not await self.pixiv_client_wrapper.authenticate()
            ):
                if self.pixiv_config and hasattr(
                    self.pixiv_config, "get_auth_error_message"
                ):
                    return self.pixiv_config.get_auth_error_message()
                return "Pixiv API 认证失败，请检查配置中的凭据信息。"

            tags = query.strip()
            return await self._search_novel(tags, query, context)

        except Exception as e:
            logger.error(f"Pixiv小说搜索失败: {e}")
            return f"搜索失败: {str(e)}"

    async def _search_novel(self, tags, query, context):
        import asyncio

        # ID 检查
        if query.isdigit():
            logger.info(f"检测到小说ID {query}")
            try:
                novel_detail = await _call_pixiv_api(
                    self.pixiv_client_wrapper,
                    self.pixiv_client.novel_detail, int(query)
                )
                if novel_detail and novel_detail.novel:
                    event = self._get_event(context)
                    if event:
                        return await self._send_novel_result(
                            event, [novel_detail.novel], query, tags
                        )
                    else:
                        return f"找到小说: {novel_detail.novel.title} (ID: {query})，但无法发送文件(无事件上下文)。"
                else:
                    return f"未找到ID为 {query} 的小说。"
            except Exception as e:
                return f"获取小说详情失败: {str(e)}"

        # 标签搜索
        try:
            search_result = await _call_pixiv_api(
                self.pixiv_client_wrapper,
                self.pixiv_client.search_novel,
                tags,
                search_target="partial_match_for_tags",
            )

            if search_result and search_result.novels:
                event = self._get_event(context)
                if event:
                    return await self._send_novel_result(
                        event, search_result.novels, query, tags
                    )
                else:
                    return self._format_text_results(search_result.novels, query, tags)
            else:
                return f"未找到关于 '{query}' 的小说。"
        except Exception as e:
            return f"API调用错误: {str(e)}"

    async def _send_novel_result(self, event, items, query, tags):
        import asyncio

        if not items:
            return "未找到小说。"

        selected_item = items[0]  # 取第一个
        novel_id = str(selected_item.id)
        novel_title = selected_item.title

        logger.info(f"准备下载小说 {novel_title} (ID: {novel_id})")

        try:
            novel_content_result = await _call_pixiv_api(
                self.pixiv_client_wrapper,
                self.pixiv_client.webview_novel, novel_id
            )
            if not novel_content_result or not hasattr(novel_content_result, "text"):
                return f"无法获取小说内容 (ID: {novel_id})。"

            novel_text = novel_content_result.text

            try:
                pdf_bytes = await asyncio.to_thread(
                    self._create_pdf_from_text, novel_title, novel_text
                )
            except FileNotFoundError:
                return "无法生成PDF：字体文件丢失。"
            except Exception as e:
                return f"生成PDF失败: {str(e)}"

            # 加密
            password = hashlib.md5(novel_id.encode()).hexdigest()
            final_pdf_bytes = pdf_bytes
            password_notice = ""
            try:
                from PyPDF2 import PdfReader, PdfWriter

                reader = PdfReader(io.BytesIO(pdf_bytes))
                writer = PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                writer.encrypt(password)
                with io.BytesIO() as bs:
                    writer.write(bs)
                    final_pdf_bytes = bs.getvalue()
                password_notice = f"PDF已加密，密码: {password}"
            except Exception:
                password_notice = "PDF未加密。"

            # 发送
            safe_title = generate_safe_filename(novel_title, "novel")
            file_name = f"{safe_title}_{novel_id}.pdf"

            file_sent = False
            if event.get_platform_name() == "aiocqhttp" and event.get_group_id():
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                        AiocqhttpMessageEvent,
                    )

                    if isinstance(event, AiocqhttpMessageEvent):
                        client_bot = event.bot
                        group_id = event.get_group_id()
                        file_base64 = base64.b64encode(final_pdf_bytes).decode("utf-8")
                        await client_bot.upload_group_file(
                            group_id=group_id,
                            file=f"base64://{file_base64}",
                            name=file_name,
                        )
                        file_sent = True
                except Exception as e:
                    logger.error(f"群文件上传失败: {e}")

            author = (
                getattr(selected_item.user, "name", "未知作者")
                if hasattr(selected_item, "user")
                else "未知作者"
            )

            if file_sent:
                return f"已下载小说：\n**{novel_title}** - {author}\nID: {novel_id}\n文件已上传到群文件。\n{password_notice}\n(任务完成)"
            else:
                return f"已找到小说：\n**{novel_title}** - {author}\nID: {novel_id}\n无法发送文件，请尝试手动下载。\n(任务完成)"

        except Exception as e:
            logger.error(f"处理小说失败: {e}")
            return f"处理小说失败: {str(e)}"

    def _create_pdf_from_text(self, title: str, text: str) -> bytes:
        font_path = Path(__file__).parent.parent / "data" / "SmileySans-Oblique.ttf"
        if not font_path.exists():
            raise FileNotFoundError(f"字体文件不存在: {font_path}")

        pdf = FPDF()
        pdf.add_page()
        pdf.add_font("SmileySans", "", str(font_path), uni=True)
        pdf.set_font("SmileySans", size=20)
        pdf.multi_cell(0, 10, title, align="C")
        pdf.ln(10)
        pdf.set_font_size(12)
        pdf.multi_cell(0, 10, text)
        return pdf.output(dest="S")

    def _get_event(self, context):
        try:
            agent_context = context.context if hasattr(context, "context") else context
            if hasattr(context, "event") and context.event:
                return context.event
            elif hasattr(agent_context, "event") and agent_context.event:
                return agent_context.event
        except Exception:
            pass
        return None

    def _format_text_results(self, items, query, tags):
        result = "找到以下小说:\n"
        for i, item in enumerate(items[:5], 1):
            title = getattr(item, "title", "未知标题")
            result += f"{i}. {title} (ID: {item.id})\n"
        return result


def create_pixiv_llm_tools(
    pixiv_client=None, pixiv_config=None, pixiv_client_wrapper=None
) -> List[FunctionTool]:
    """
    创建Pixiv相关的LLM工具列表
    """
    logger.info(
        "创建Pixiv LLM工具，pixiv_client: %s, wrapper: %s"
        % (
            "已设置" if pixiv_client else "未设置",
            "已设置" if pixiv_client_wrapper else "未设置",
        )
    )

    tool_impls = [
        PixivIllustSearchTool(
            pixiv_client=pixiv_client,
            pixiv_config=pixiv_config,
            pixiv_client_wrapper=pixiv_client_wrapper,
        ),
        PixivNovelSearchTool(
            pixiv_client=pixiv_client,
            pixiv_config=pixiv_config,
            pixiv_client_wrapper=pixiv_client_wrapper,
        ),
    ]
    logger.info(f"已创建 {len(tool_impls)} 个LLM工具")
    return tool_impls
