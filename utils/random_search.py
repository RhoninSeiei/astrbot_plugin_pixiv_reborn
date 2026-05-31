import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain

from .database import (
    get_all_random_search_groups,
    get_random_tags,
    filter_sent_illusts,
    add_sent_illust,
    cleanup_old_sent_illusts,
    get_schedule_time,
    set_schedule_time,
    remove_schedule_time,
    get_all_schedule_times,
    get_all_random_ranking_groups,
    get_random_rankings,
    get_random_search_group_config,
    add_random_search_send_attempt,
    try_claim_random_search_execution,
    release_random_search_execution,
)
from .tag import (
    build_detail_message,
    FilterConfig,
    validate_and_process_tags,
    filter_illusts_with_reason,
)
from .pixiv_utils import send_pixiv_image, cleanup_pixiv_temp_files
from .random_empty_retry import (
    build_retry_source_sequence,
    enforce_random_push_delivery_policy,
    is_random_push_image_failure_notice,
    resolve_retry_depth,
)
from .random_schedule import normalize_schedule_time
from .random_group_config import resolve_random_search_runtime_config


@dataclass
class RandomSearchExecutionResult:
    completed: bool = True
    had_sendable_candidates: bool = False
    sent_count: int = 0


class RandomSearchService:
    EXECUTION_LEASE_MINUTES = 30
    RANDOM_IMAGE_SEND_RETRIES = 3
    RANDOM_IMAGE_FAILURE_STREAK_LIMIT = 2

    def __init__(self, client_wrapper, pixiv_config, context):
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client_api
        self.pixiv_config = pixiv_config
        self.context = context

        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self.job = None
        # 使用数据库存储调度时间，不再使用内存字典
        # 防止并发执行的锁: {chat_id: bool}
        self.execution_locks = {}

        self.global_execution_lock = asyncio.Lock()  # 全局执行锁
        self.task_queue = asyncio.Queue()  # 任务队列
        self.is_queue_processor_running = False  # 队列处理器运行状态
        self._queue_processor_task: asyncio.Task | None = None
        self._is_running = False

    def start(self):
        """启动后台任务"""
        if not self.scheduler.running:
            self._is_running = True
            self.job = self.scheduler.add_job(
                self._scheduler_tick,
                "interval",
                minutes=1,  # 心跳检查间隔：每分钟检查一次是否有群组达到随机推送时间
                next_run_time=datetime.now() + timedelta(seconds=10),
            )
            # 添加定期清理任务，每天清理一次过期记录
            self.scheduler.add_job(
                self._cleanup_task,
                "cron",
                hour=2,
                minute=0,  # 每天凌晨2点执行
            )

            self.scheduler.start()
            logger.info("Pixiv 随机搜索服务已启动。")

            # 服务启动时，从数据库加载所有调度时间
            self._load_existing_schedules()

    def _load_existing_schedules(self):
        """从数据库加载现有的调度时间"""
        try:
            schedules = get_all_schedule_times()
            logger.info(f"从数据库加载了 {len(schedules)} 个群组的调度时间")
        except Exception as e:
            logger.error(f"加载调度时间失败: {e}")

    async def stop(self):
        """停止后台任务"""
        self._is_running = False
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Pixiv 随机搜索服务已停止。")

        if self._queue_processor_task and not self._queue_processor_task.done():
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"等待随机搜索队列处理器停止时出错: {e}")
        self._queue_processor_task = None
        self.is_queue_processor_running = False

    def _normalize_schedule_time(self, candidate: datetime) -> datetime:
        return normalize_schedule_time(
            candidate,
            getattr(self.pixiv_config, "random_search_quiet_start", "00:00"),
            getattr(self.pixiv_config, "random_search_quiet_end", "08:00"),
            enabled=getattr(
                self.pixiv_config, "random_search_quiet_hours_enabled", False
            ),
        )

    def _set_schedule_time(self, chat_id: str, candidate: datetime, reason: str):
        normalized = self._normalize_schedule_time(candidate)
        set_schedule_time(chat_id, normalized)
        if normalized != candidate:
            logger.info(
                f"群组 {chat_id}: {reason}处于随机搜索静默时段，已调整到 {normalized}"
            )
        return normalized

    def _resolve_group_runtime_config(self, chat_id: str):
        return resolve_random_search_runtime_config(
            self.pixiv_config, get_random_search_group_config(chat_id)
        )

    def _get_group_interval_range(self, chat_id: str):
        group_config = self._resolve_group_runtime_config(chat_id)
        return group_config.min_interval_minutes, group_config.max_interval_minutes

    def _empty_retry_enabled(self) -> bool:
        return bool(
            getattr(self.pixiv_config, "random_search_empty_retry_enabled", True)
        )

    def _empty_retry_extra_depth(self) -> int:
        value = getattr(self.pixiv_config, "random_search_empty_retry_extra_depth", 3)
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 3

    def _empty_retry_sources(self) -> int:
        value = getattr(self.pixiv_config, "random_search_empty_retry_sources", 1)
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 1

    def _claim_execution(self, chat_id: str, now: datetime = None) -> str | None:
        current_time = now or datetime.now()
        owner_token = f"{id(self)}:{uuid4().hex}"
        expires_at = current_time + timedelta(minutes=self.EXECUTION_LEASE_MINUTES)
        claimed = try_claim_random_search_execution(
            chat_id,
            now=current_time,
            expires_at=expires_at,
            owner_token=owner_token,
        )
        if claimed:
            return owner_token
        return None

    def _schedule_next_run_from_now(self, chat_id: str, now: datetime, reason: str):
        min_interval, max_interval = self._get_group_interval_range(chat_id)
        next_interval = random.randint(min_interval, max_interval)
        new_execution_time = now + timedelta(minutes=next_interval)
        return self._set_schedule_time(chat_id, new_execution_time, reason)

    def _build_filter_config(self, display_tag_str: str, exclude_tags, chat_id: str):
        group_config = self._resolve_group_runtime_config(chat_id)
        return FilterConfig(
            **enforce_random_push_delivery_policy(
                {
                    "r18_mode": self.pixiv_config.r18_mode,
                    "filter_r18g_only": self.pixiv_config.filter_r18g_only,
                    "ai_filter_mode": self.pixiv_config.ai_filter_mode,
                    "ai_detection_mode": self.pixiv_config.ai_detection_mode,
                    "display_tag_str": display_tag_str,
                    "return_count": group_config.return_count,
                    "logger": logger,
                    "show_filter_result": self.pixiv_config.show_filter_result,
                    "single_response_mode": self.pixiv_config.single_response_mode,
                    "excluded_tags": exclude_tags or [],
                    "forward_threshold": self.pixiv_config.forward_threshold,
                    "show_details": self.pixiv_config.show_details,
                    "min_likes": group_config.min_likes,
                }
            )
        )

    def _make_mock_event(self):
        class MockEvent:
            def __init__(self):
                self.bot = None

            def chain_result(self, chain):
                message_chain = MessageChain()
                message_chain.chain = chain
                return message_chain

            def plain_result(self, text):
                message_chain = MessageChain()
                message_chain.message(text)
                return message_chain

            def get_platform_name(self):
                return "unknown"

            def get_group_id(self):
                return None

        return MockEvent()

    def _is_send_timeout_after_accept(self, error) -> bool:
        """判断 QQ sendMsg 是否在受理后等待消息更新回执超时。"""
        text_parts = [repr(error), str(error)]
        for attr in ("retcode", "message", "wording", "status"):
            value = getattr(error, attr, None)
            if value is not None:
                text_parts.append(str(value))
        text = chr(10).join(text_parts).replace('\\"', '"')
        compact_text = "".join(text.split())

        has_timeout_retcode = (
            getattr(error, "retcode", None) == 1200
            or "retcode=1200" in text
            or "retcode': 1200" in text
            or '"retcode": 1200' in text
        )
        has_send_timeout = "Timeout:" in text and "sendMsg" in text
        has_success_event = (
            '"result":0' in compact_text and '"errMsg":""' in compact_text
        )
        return has_timeout_retcode and has_send_timeout and has_success_event

    async def _send_message_with_attempt_record(
        self,
        chat_id: str,
        session_id: str,
        source_type: str,
        source_name: str,
        message_content,
        related_illust_ids,
        success_log: str,
        failure_log: str,
    ) -> set[int]:
        """发送消息并记录本次随机搜索发送尝试。"""
        if not message_content:
            return set()

        illust_ids = list(related_illust_ids or [])
        attempt_illust_ids = illust_ids or [None]

        try:
            if hasattr(message_content, "chain"):
                await self.context.send_message(session_id, message_content)
            elif isinstance(message_content, MessageChain):
                await self.context.send_message(session_id, message_content)
            elif isinstance(message_content, list):
                raise TypeError("random_search 收到 list 类型消息内容，无法发送")
            else:
                chain = MessageChain().message(str(message_content))
                await self.context.send_message(session_id, chain)

            for illust_id in attempt_illust_ids:
                add_random_search_send_attempt(
                    chat_id=chat_id,
                    session_id=session_id,
                    source_type=source_type,
                    source_name=source_name,
                    illust_id=illust_id,
                    success=True,
                )
            logger.info(success_log)
            return set(illust_ids)
        except Exception as e:
            if self._is_send_timeout_after_accept(e):
                for illust_id in attempt_illust_ids:
                    add_random_search_send_attempt(
                        chat_id=chat_id,
                        session_id=session_id,
                        source_type=source_type,
                        source_name=source_name,
                        illust_id=illust_id,
                        success=True,
                    )
                logger.warning(
                    f"{failure_log}: QQ sendMsg 已受理但等待消息更新回执超时，按已发送处理: {e}"
                )
                return set(illust_ids)

            for illust_id in attempt_illust_ids:
                add_random_search_send_attempt(
                    chat_id=chat_id,
                    session_id=session_id,
                    source_type=source_type,
                    source_name=source_name,
                    illust_id=illust_id,
                    success=False,
                    error_message=str(e),
                )
            logger.error(f"{failure_log}: {e}")
            return set()
        finally:
            await cleanup_pixiv_temp_files(message_content)

    def _get_illust_id(self, item) -> int | None:
        try:
            item_id = getattr(item, "id", None)
            if item_id is not None:
                return int(item_id)
        except Exception:
            pass
        try:
            if isinstance(item, dict) and "id" in item:
                return int(item["id"])
        except Exception:
            pass
        return None

    async def _send_random_illust_with_retry(
        self,
        chat_id: str,
        session_id: str,
        source_type: str,
        source_name: str,
        illust,
        config: FilterConfig,
    ) -> set[int]:
        illust_id = self._get_illust_id(illust)
        related_ids = [illust_id] if illust_id is not None else []
        detail_message = build_detail_message(illust, is_novel=False)

        for attempt in range(1, self.RANDOM_IMAGE_SEND_RETRIES + 1):
            yielded_message = False
            try:
                mock_event = self._make_mock_event()
                async for message_content in send_pixiv_image(
                    self.client,
                    mock_event,
                    illust,
                    detail_message,
                    show_details=config.show_details,
                ):
                    yielded_message = True
                    if is_random_push_image_failure_notice(message_content):
                        await cleanup_pixiv_temp_files(message_content)
                        logger.warning(
                            "随机推送图片下载失败，作品 %s 第 %s/%s 次尝试失败。"
                            % (
                                illust_id or "unknown",
                                attempt,
                                self.RANDOM_IMAGE_SEND_RETRIES,
                            )
                        )
                        continue

                    logger.info(f"准备向 session_id: {session_id} 发送消息")
                    if hasattr(message_content, "chain"):
                        logger.info(f"消息链长度: {len(message_content.chain)}")

                    sent_ids = await self._send_message_with_attempt_record(
                        chat_id=chat_id,
                        session_id=session_id,
                        source_type=source_type,
                        source_name=source_name,
                        message_content=message_content,
                        related_illust_ids=related_ids,
                        success_log=f"消息已发送至 {session_id}",
                        failure_log=f"向 {session_id} 发送消息失败",
                    )
                    if sent_ids:
                        return sent_ids

                    logger.warning(
                        "随机推送消息发送失败，作品 %s 第 %s/%s 次尝试失败。"
                        % (
                            illust_id or "unknown",
                            attempt,
                            self.RANDOM_IMAGE_SEND_RETRIES,
                        )
                    )

                if not yielded_message:
                    logger.warning(
                        "随机推送未生成可发送消息，作品 %s 第 %s/%s 次尝试失败。"
                        % (
                            illust_id or "unknown",
                            attempt,
                            self.RANDOM_IMAGE_SEND_RETRIES,
                        )
                    )
            except Exception as e:
                logger.warning(
                    "随机推送处理作品 %s 第 %s/%s 次尝试异常: %s"
                    % (
                        illust_id or "unknown",
                        attempt,
                        self.RANDOM_IMAGE_SEND_RETRIES,
                        e,
                    )
                )

            if attempt < self.RANDOM_IMAGE_SEND_RETRIES:
                await asyncio.sleep(1)

        add_random_search_send_attempt(
            chat_id=chat_id,
            session_id=session_id,
            source_type=source_type,
            source_name=source_name,
            illust_id=illust_id,
            success=False,
            error_message="图片下载或消息发送连续失败，随机推送已静默回退",
        )
        logger.warning(
            "随机推送作品 %s 连续 %s 次下载或发送失败，准备尝试其他候选作品。"
            % (illust_id or "unknown", self.RANDOM_IMAGE_SEND_RETRIES)
        )
        return set()

    async def _send_random_illusts_with_fallback(
        self,
        chat_id: str,
        session_id: str,
        source_type: str,
        source_name: str,
        initial_illusts,
        config: FilterConfig,
    ) -> RandomSearchExecutionResult:
        filtered_illusts, filter_msgs = filter_illusts_with_reason(
            initial_illusts, config
        )
        logger.info(
            "随机搜索条件过滤统计: 群组 %s, 来源 %s:%s, 待过滤 %s 个，通过 %s 个，剔除 %s 个。"
            % (
                chat_id,
                source_type,
                source_name,
                len(initial_illusts),
                len(filtered_illusts),
                len(initial_illusts) - len(filtered_illusts),
            )
        )

        if not filtered_illusts:
            if filter_msgs:
                for msg in filter_msgs:
                    logger.info(f"随机搜索候选为空: {msg}")
            else:
                logger.info("随机搜索候选为空: 共享过滤后无可发送作品")
            return RandomSearchExecutionResult(had_sendable_candidates=False)

        candidates = list(filtered_illusts)
        random.shuffle(candidates)

        try:
            target_count = max(0, int(config.return_count))
        except (TypeError, ValueError):
            target_count = 1

        if target_count <= 0:
            logger.info("随机推送 return_count 小于等于 0，本次无作品发送。")
            return RandomSearchExecutionResult(had_sendable_candidates=True)

        sent_illust_ids = set()
        consecutive_failures = 0

        for illust in candidates:
            if len(sent_illust_ids) >= target_count:
                break

            sent_ids = await self._send_random_illust_with_retry(
                chat_id=chat_id,
                session_id=session_id,
                source_type=source_type,
                source_name=source_name,
                illust=illust,
                config=config,
            )
            if sent_ids:
                sent_illust_ids.update(sent_ids)
                consecutive_failures = 0
                continue

            consecutive_failures += 1
            if consecutive_failures >= self.RANDOM_IMAGE_FAILURE_STREAK_LIMIT:
                logger.warning(
                    "随机推送连续 %s 个候选作品失败，已静默停止本次推送: chat_id=%s, source=%s:%s"
                    % (
                        self.RANDOM_IMAGE_FAILURE_STREAK_LIMIT,
                        chat_id,
                        source_type,
                        source_name,
                    )
                )
                break

        for illust_id in sent_illust_ids:
            add_sent_illust(illust_id, chat_id)
        if sent_illust_ids:
            logger.info(f"群组 {chat_id}: 已记录 {len(sent_illust_ids)} 个作品的发送记录")

        return RandomSearchExecutionResult(
            had_sendable_candidates=True,
            sent_count=len(sent_illust_ids),
        )

    async def _scheduler_tick(self):
        """
        检查是否有群组需要执行搜索，并将其加入队列。
        """
        if not self.client or not self._is_running:
            return

        try:
            # 启动队列处理器（如果尚未运行）
            if (
                not self._queue_processor_task
                or self._queue_processor_task.done()
                or not self.is_queue_processor_running
            ):
                self._queue_processor_task = asyncio.create_task(
                    self._task_queue_processor()
                )
                self.is_queue_processor_running = True
                logger.info("RandomSearchService 队列处理器已启动")

            # 获取所有配置了标签的群组
            # groups = get_all_random_search_groups()
            tag_groups = get_all_random_search_groups()
            ranking_groups = get_all_random_ranking_groups()
            groups = list(set(tag_groups + ranking_groups))

            now = datetime.now()

            pending_tasks = []

            for chat_id in groups:
                # 初始化执行锁
                if chat_id not in self.execution_locks:
                    self.execution_locks[chat_id] = False

                # 从数据库获取下次执行时间
                next_execution_time = get_schedule_time(chat_id)

                # 如果是第一次看到这个群组，立即或稍后调度
                if next_execution_time is None:
                    # 初始延迟，避免同时启动，使用用户配置的间隔范围
                    min_interval, max_interval = self._get_group_interval_range(
                        chat_id
                    )

                    delay_minutes = random.randint(min_interval, max_interval)
                    next_execution_time = now + timedelta(minutes=delay_minutes)
                    scheduled_time = self._set_schedule_time(
                        chat_id, next_execution_time, "首次调度时间"
                    )
                    logger.info(
                        f"群组 {chat_id}: 首次调度随机搜索，将在 {scheduled_time} 执行"
                    )
                    continue

                # 检查是否到了运行时间且当前没有执行任务
                if now >= next_execution_time and not self.execution_locks[chat_id]:
                    next_allowed_time = self._normalize_schedule_time(now)
                    if next_allowed_time != now:
                        set_schedule_time(chat_id, next_allowed_time)
                        logger.info(
                            f"群组 {chat_id}: 当前处于随机搜索静默时段，推迟到 {next_allowed_time}"
                        )
                        continue
                    claim_token = self._claim_execution(chat_id, now)
                    if not claim_token:
                        logger.info(f"群组 {chat_id}: 已有随机搜索执行领取，跳过本次入队")
                        continue
                    scheduled_time = self._schedule_next_run_from_now(
                        chat_id, now, "防重预占调度时间"
                    )
                    logger.info(
                        f"群组 {chat_id}: 已预占随机搜索执行权，下次候选运行时间为 {scheduled_time}"
                    )
                    pending_tasks.append((chat_id, claim_token))

            # 将所有待执行的群组加入队列
            for task_item in pending_tasks:
                chat_id, claim_token = task_item
                try:
                    await self.task_queue.put(task_item)
                    logger.info(f"群组 {chat_id}: 已加入随机搜索队列")
                except Exception as e:
                    release_random_search_execution(chat_id, claim_token)
                    logger.error(f"将群组 {chat_id} 加入队列失败: {e}")

        except Exception as e:
            logger.error(f"RandomSearchService 调度器 tick 出错: {e}")

    async def _task_queue_processor(self):
        """
        任务队列处理器，按顺序执行队列中的搜索任务。
        """
        logger.info("RandomSearchService 任务队列处理器开始运行")
        try:
            while self._is_running:
                try:
                    # 从队列中获取群组ID（阻塞等待）
                    task_item = await self.task_queue.get()
                    if isinstance(task_item, tuple):
                        chat_id, claim_token = task_item
                    else:
                        chat_id = task_item
                        claim_token = None

                    # 使用全局锁确保同时只有一个任务执行
                    async with self.global_execution_lock:
                        # 再次检查群组是否仍在执行状态
                        if self.execution_locks.get(chat_id, False):
                            logger.warning(f"群组 {chat_id} 已在执行状态，跳过本次任务")
                            if claim_token:
                                release_random_search_execution(chat_id, claim_token)
                            self.task_queue.task_done()
                            continue

                        if claim_token is None:
                            claim_token = self._claim_execution(chat_id)
                            if not claim_token:
                                logger.info(
                                    f"群组 {chat_id}: 已有随机搜索执行领取，跳过本次任务"
                                )
                                self.task_queue.task_done()
                                continue

                        # 设置执行锁
                        self.execution_locks[chat_id] = True

                        try:
                            logger.info(f"开始执行群组 {chat_id} 的随机搜索")
                            await self.execute_search_for_group(chat_id)

                            # 调度下次运行
                            now = datetime.now()
                            scheduled_time = self._schedule_next_run_from_now(
                                chat_id, now, "下次调度时间"
                            )
                            logger.info(
                                f"群组 {chat_id}: 随机搜索已执行。下次运行时间为 {scheduled_time}。"
                            )

                        except Exception as e:
                            logger.error(f"执行群组 {chat_id} 的随机搜索时出错: {e}")
                        finally:
                            # 释放执行锁
                            self.execution_locks[chat_id] = False
                            release_random_search_execution(chat_id, claim_token)
                            self.task_queue.task_done()

                except asyncio.CancelledError:
                    logger.info("RandomSearchService 任务队列处理器被取消")
                    break
                except Exception as e:
                    logger.error(f"RandomSearchService 任务队列处理器出错: {e}")
                    # 短暂延迟后继续处理下一个任务
                    await asyncio.sleep(5)
        finally:
            self.is_queue_processor_running = False
            self._queue_processor_task = None

    async def _cleanup_task(self):
        """定期清理过期记录的任务"""
        try:
            logger.info("开始清理过期的已发送作品记录...")
            # 获取配置
            days = self.pixiv_config.random_sent_illust_retention_days

            # 使用 to_thread 防止数据库操作阻塞异步循环
            await asyncio.to_thread(cleanup_old_sent_illusts, days=days)
            logger.info("清理过期记录任务完成。")
        except Exception as e:
            logger.error(f"清理过期记录任务出错: {e}")

    async def execute_search_for_group(self, chat_id: str) -> int:
        """为特定群组执行随机搜索（标签或排行榜）"""
        tags = get_random_tags(chat_id)
        rankings = get_random_rankings(chat_id)

        if not tags and not rankings:
            return 0

        # 随机选择执行标签搜索或排行榜搜索
        all_options = []
        for tag in tags:
            all_options.append(("tag", tag))
        for ranking in rankings:
            all_options.append(("ranking", ranking))

        selected = random.choice(all_options)
        if self._empty_retry_enabled():
            source_sequence = build_retry_source_sequence(
                all_options, selected, self._empty_retry_sources()
            )
        else:
            source_sequence = [selected]

        first_option = source_sequence[0]

        for index, option in enumerate(source_sequence):
            if index > 0:
                logger.info(
                    f"群组 {chat_id}: 前一个随机搜索来源无可发送作品，尝试补抽其他来源"
                )

            if option[0] == "tag":
                result = await self._execute_tag_search(chat_id, option[1])
            else:
                result = await self._execute_ranking_search(chat_id, option[1])

            if not result.completed:
                return result.sent_count
            if result.had_sendable_candidates:
                return result.sent_count

        logger.info(
            f"群组 {chat_id}: 随机搜索所有来源均无可发送作品，已静默结束本次任务"
        )
        return 0

    def _option_name(self, option) -> str:
        if option[0] == "tag":
            return option[1].tag
        return option[1].mode

    def _option_session_id(self, option) -> str:
        return option[1].session_id

    async def _send_no_result_notice(self, chat_id: str, option):
        source_type = option[0]
        source_name = self._option_name(option)
        session_id = self._option_session_id(option)
        message_content = MessageChain().message("没有找到符合条件的作品。")
        await self._send_message_with_attempt_record(
            chat_id=chat_id,
            session_id=session_id,
            source_type=source_type,
            source_name=source_name,
            message_content=message_content,
            related_illust_ids=[],
            success_log=f"随机搜索空结果提示已发送至 {session_id}",
            failure_log=f"向 {session_id} 发送随机搜索空结果提示失败",
        )

    async def _fetch_tag_illusts(
        self,
        raw_tag: str,
        search_params: dict,
        page_limit: int,
        all_illusts=None,
        page_count: int = 0,
        next_params=None,
    ):
        all_illusts = list(all_illusts or [])
        next_params = next_params or search_params.copy()

        while next_params:
            if page_limit > 0 and page_count >= page_limit:
                break

            json_result = await asyncio.to_thread(
                self.client.search_illust, **next_params
            )

            if not json_result or not hasattr(json_result, "illusts"):
                break

            current_illusts = json_result.illusts
            if current_illusts:
                all_illusts.extend(current_illusts)
                page_count += 1
                logger.info(
                    f"标签 {raw_tag} 的随机搜索：已获取第 {page_count} 页，找到 {len(current_illusts)} 个插画"
                )

                if page_count % 3 == 0:
                    logger.info(
                        f"标签 {raw_tag} 搜索进行中：已获取 {page_count} 页，共 {len(all_illusts)} 个结果..."
                    )
            else:
                break

            next_url = json_result.next_url
            next_params = self.client.parse_qs(next_url) if next_url else None

            if next_params:
                await asyncio.sleep(0.5)

        return all_illusts, page_count, next_params

    async def _send_tag_search_results(
        self,
        chat_id: str,
        session_id: str,
        raw_tag: str,
        display_tags: str,
        exclude_tags,
        all_illusts,
    ) -> RandomSearchExecutionResult:
        initial_illusts = filter_sent_illusts(all_illusts, chat_id)
        logger.info(
            "标签 %s 的随机搜索发送缓存过滤统计: 累计结果 %s 个，已发送缓存过滤 %s 个，待条件过滤 %s 个。"
            % (
                raw_tag,
                len(all_illusts),
                len(all_illusts) - len(initial_illusts),
                len(initial_illusts),
            )
        )

        if not initial_illusts:
            logger.info(f"标签 {raw_tag} 的随机搜索过滤已发送记录后无可用作品。")
            return RandomSearchExecutionResult(had_sendable_candidates=False)

        config = self._build_filter_config(
            display_tag_str=f"随机:{display_tags}",
            exclude_tags=exclude_tags,
            chat_id=chat_id,
        )
        result = await self._send_random_illusts_with_fallback(
            chat_id=chat_id,
            session_id=session_id,
            source_type="tag",
            source_name=raw_tag,
            initial_illusts=initial_illusts,
            config=config,
        )
        if not result.had_sendable_candidates:
            logger.info(f"标签 {raw_tag} 的随机搜索共享过滤后无可用作品。")
        return result

    async def _execute_tag_search(
        self, chat_id: str, selected_tag_entry
    ) -> RandomSearchExecutionResult:
        """执行标签搜索"""
        raw_tag = selected_tag_entry.tag
        session_id = selected_tag_entry.session_id

        logger.info(f"正在为群组 {chat_id} 执行随机标签搜索，标签: {raw_tag}")

        # 如果需要则认证
        if not await self.client_wrapper.authenticate():
            logger.error(f"群组 {chat_id} 的随机搜索失败: 认证失败。")
            return RandomSearchExecutionResult(completed=False)

        # 处理标签
        tag_result = validate_and_process_tags(raw_tag)
        if not tag_result["success"]:
            logger.warning(
                f"标签 {raw_tag} 的随机搜索验证失败: {tag_result['error_message']}"
            )
            return RandomSearchExecutionResult(completed=False)

        search_tags = tag_result["search_tags"]
        exclude_tags = tag_result["exclude_tags"]
        display_tags = tag_result["display_tags"]

        try:
            # 准备搜索参数，参考 pixiv_deepsearch 的实现
            search_params = {
                "word": search_tags,
                "search_target": "partial_match_for_tags",
                "sort": "popular_desc",
                "filter": "for_ios",
                "req_auth": True,
            }

            deep_search_depth = self.pixiv_config.deep_search_depth
            all_illusts, page_count, next_params = await self._fetch_tag_illusts(
                raw_tag, search_params, deep_search_depth
            )

            if not all_illusts:
                logger.info(f"标签 {raw_tag} 的随机搜索未返回结果。")
                return RandomSearchExecutionResult(had_sendable_candidates=False)

            # 记录找到的总数量，与 pixiv_deepsearch 保持一致
            initial_count = len(all_illusts)
            logger.info(
                f"标签 {raw_tag} 的随机搜索完成，共获取 {page_count} 页，找到 {initial_count} 个插画，开始过滤处理..."
            )

            result = await self._send_tag_search_results(
                chat_id,
                session_id,
                raw_tag,
                display_tags,
                exclude_tags,
                all_illusts,
            )
            if result.had_sendable_candidates:
                return result

            current_depth = deep_search_depth
            while self._empty_retry_enabled() and next_params:
                retry_depth = resolve_retry_depth(
                    current_depth, self._empty_retry_extra_depth()
                )
                if retry_depth == current_depth:
                    break
                logger.info(
                    f"标签 {raw_tag} 在前 {page_count} 页无可发送作品，扩大搜索范围到 {retry_depth} 页"
                )
                all_illusts, page_count, next_params = await self._fetch_tag_illusts(
                    raw_tag,
                    search_params,
                    retry_depth,
                    all_illusts=all_illusts,
                    page_count=page_count,
                    next_params=next_params,
                )
                logger.info(
                    f"标签 {raw_tag} 的扩大搜索完成，共获取 {page_count} 页，找到 {len(all_illusts)} 个插画，重新过滤处理..."
                )
                result = await self._send_tag_search_results(
                    chat_id,
                    session_id,
                    raw_tag,
                    display_tags,
                    exclude_tags,
                    all_illusts,
                )
                if result.had_sendable_candidates:
                    return result
                current_depth = retry_depth

            return result

        except Exception as e:
            logger.error(f"为群组 {chat_id} 执行随机标签搜索时出错: {e}")
            return RandomSearchExecutionResult(completed=False)

    async def _execute_ranking_search(
        self, chat_id: str, ranking_config
    ) -> RandomSearchExecutionResult:
        """执行排行榜搜索"""
        mode = ranking_config.mode
        date = ranking_config.date
        session_id = ranking_config.session_id

        logger.info(
            f"正在为群组 {chat_id} 执行随机排行榜搜索，模式: {mode}, 日期: {date}"
        )

        if not await self.client_wrapper.authenticate():
            logger.error(f"群组 {chat_id} 的随机排行榜搜索失败: 认证失败。")
            return RandomSearchExecutionResult(completed=False)

        try:
            ranking_result = await asyncio.to_thread(
                self.client.illust_ranking, mode=mode, date=date
            )
            initial_illusts = ranking_result.illusts if ranking_result.illusts else []

            if not initial_illusts:
                logger.info(f"排行榜 {mode} 的随机搜索未返回结果。")
                return RandomSearchExecutionResult(had_sendable_candidates=False)

            # Pixiv 排行榜接口在非 manga 模式下也可能混入 type=manga 的作品，这里主动过滤掉
            if mode and "manga" not in str(mode).lower():
                before_count = len(initial_illusts)
                initial_illusts = [
                    i for i in initial_illusts if getattr(i, "type", None) != "manga"
                ]
                filtered_count = before_count - len(initial_illusts)
                if filtered_count:
                    logger.info(
                        f"排行榜 {mode}：已过滤 {filtered_count} 个漫画作品(manga)。"
                    )

            # 过滤已发送的作品
            before_sent_filter_count = len(initial_illusts)
            initial_illusts = filter_sent_illusts(initial_illusts, chat_id)
            logger.info(
                "排行榜 %s 的随机搜索发送缓存过滤统计: 累计结果 %s 个，已发送缓存过滤 %s 个，待条件过滤 %s 个。"
                % (
                    mode,
                    before_sent_filter_count,
                    before_sent_filter_count - len(initial_illusts),
                    len(initial_illusts),
                )
            )

            if not initial_illusts:
                logger.info(f"排行榜 {mode} 的随机搜索过滤后无可用作品。")
                return RandomSearchExecutionResult(had_sendable_candidates=False)

            config = self._build_filter_config(
                display_tag_str=f"随机排行榜:{mode}",
                exclude_tags=[],
                chat_id=chat_id,
            )
            result = await self._send_random_illusts_with_fallback(
                chat_id=chat_id,
                session_id=session_id,
                source_type="ranking",
                source_name=mode,
                initial_illusts=initial_illusts,
                config=config,
            )
            if not result.had_sendable_candidates:
                logger.info(f"排行榜 {mode} 的随机搜索共享过滤后无可用作品。")
            return result

        except Exception as e:
            logger.error(f"为群组 {chat_id} 执行随机排行榜搜索时出错: {e}")
            return RandomSearchExecutionResult(completed=False)

    def suspend_group_search(self, chat_id: str):
        """暂停指定群组的随机搜索"""
        try:
            # 移除该群组的调度时间
            remove_schedule_time(chat_id)
            logger.info(f"已移除群组 {chat_id} 的调度时间")
        except Exception as e:
            logger.error(f"移除群组 {chat_id} 调度时间失败: {e}")

    def resume_group_search(self, chat_id: str):
        """恢复指定群组的随机搜索"""
        try:
            # 重新设置调度时间，使用用户配置的间隔范围
            now = datetime.now()
            min_interval, max_interval = self._get_group_interval_range(chat_id)

            # 恢复时使用较短的延迟，但仍在用户配置范围内
            delay_minutes = random.randint(min_interval, max_interval)
            next_time = now + timedelta(minutes=delay_minutes)
            set_schedule_time(chat_id, next_time)
            logger.info(
                f"群组 {chat_id} 随机搜索已恢复，将在 {delay_minutes} 分钟后执行"
            )
        except Exception as e:
            logger.error(f"恢复群组 {chat_id} 调度时间失败: {e}")

    def get_queue_status(self) -> dict:
        """获取队列状态信息，用于调试和监控"""
        return {
            "queue_size": self.task_queue.qsize(),
            "is_queue_processor_running": self.is_queue_processor_running,
            "execution_locks": dict(self.execution_locks),
            "active_groups": [
                chat_id for chat_id, locked in self.execution_locks.items() if locked
            ],
        }

    async def force_execute_group(self, chat_id: str) -> bool:
        """强制执行指定群组的随机搜索（用于调试）"""
        if chat_id not in self.execution_locks:
            self.execution_locks[chat_id] = False

        if self.execution_locks[chat_id]:
            logger.warning(f"群组 {chat_id} 已在执行状态，无法强制执行")
            return False

        try:
            await self.task_queue.put((chat_id, None))
            logger.info(f"群组 {chat_id} 已强制加入执行队列")
            return True
        except Exception as e:
            logger.error(f"强制执行群组 {chat_id} 失败: {e}")
            return False
