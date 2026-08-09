import collections
import sys
import tempfile
import types
import typing
import unittest
from pathlib import Path


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeMessageChain:
    def __init__(self):
        self.chain = []

    def message(self, text):
        self.chain.append(FakePlain(text))
        return self


class FakeScheduler:
    running = False

    def add_job(self, *args, **kwargs):
        return object()

    def start(self):
        self.running = True

    def shutdown(self):
        self.running = False


class FakeContext:
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, session_id, message_content):
        self.sent_messages.append((session_id, message_content))


class FakeActionFailedTimeout(Exception):
    retcode = 1200
    status = "failed"
    message = (
        "Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg "
        "ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate EventRet:\n"
        "{\n"
        "    \"result\": 0,\n"
        "    \"errMsg\": \"\"\n"
        "}\n"
    )
    wording = message

    def __str__(self):
        return self.message


class FakeTimeoutContext:
    async def send_message(self, session_id, message_content):
        raise FakeActionFailedTimeout()


class FakeUser:
    name = "artist"


class FakeIllust:
    def __init__(self, illust_id, page_count=1, tags=None):
        self.id = illust_id
        self.title = f"title-{illust_id}"
        self.user = FakeUser()
        self.tags = tags or []
        self.type = "illust"
        self.page_count = page_count
        self.delivery_image_count = 3 if page_count > 9 else page_count


class FakeSearchResult:
    def __init__(self, illusts, next_url=None):
        self.illusts = illusts
        self.next_url = next_url


class FakeSearchClient:
    def __init__(self, final_page_with_candidates):
        self.calls = []
        self.final_page_with_candidates = final_page_with_candidates

    def search_illust(self, **params):
        page = int(params.get("page", 1))
        self.calls.append(page)
        first_id = page * 1000
        illusts = [FakeIllust(first_id + index) for index in range(30)]
        next_url = f"page={page + 1}" if page < self.final_page_with_candidates else None
        return FakeSearchResult(illusts, next_url=next_url)

    def parse_qs(self, next_url):
        return {"page": int(next_url.split("=", 1)[1])}


class FakeClientWrapper:
    def __init__(self):
        self.calls = []

    async def authenticate(self):
        return True

    async def call_pixiv_api(self, func, *args, **kwargs):
        self.calls.append((getattr(func, "__name__", repr(func)), args, kwargs))
        return func(*args, **kwargs)


def install_import_stubs(data_dir):
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()

    astrbot_star = types.ModuleType("astrbot.api.star")

    class FakeStarTools:
        @staticmethod
        def get_data_dir(name):
            return Path(data_dir) / name

    astrbot_star.StarTools = FakeStarTools

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.Plain = FakePlain
    message_components.Image = object
    message_components.Node = object
    message_components.Nodes = object

    message_event_result = types.ModuleType(
        "astrbot.core.message.message_event_result"
    )
    message_event_result.MessageChain = FakeMessageChain

    apscheduler = types.ModuleType("apscheduler")
    apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
    apscheduler_asyncio = types.ModuleType("apscheduler.schedulers.asyncio")
    apscheduler_asyncio.AsyncIOScheduler = lambda *args, **kwargs: FakeScheduler()

    pixivpy3 = types.ModuleType("pixivpy3")
    pixivpy3.AppPixivAPI = object

    async def fake_send_pixiv_image(*args, **kwargs):
        if False:
            yield None

    pixiv_utils = types.ModuleType("utils.pixiv_utils")
    pixiv_utils.send_pixiv_image = fake_send_pixiv_image
    pixiv_utils.cleanup_pixiv_temp_files = lambda message_content: _noop_async()
    pixiv_utils.get_illust_delivery_image_count = (
        lambda illust, send_all_pages: illust.delivery_image_count
        if send_all_pages
        else 1
    )

    database = types.ModuleType("utils.database")
    database.get_all_random_search_groups = lambda: []
    database.get_random_tags = lambda chat_id: []
    database.filter_sent_illusts = lambda illusts, chat_id: illusts
    database.add_sent_illust = lambda illust_id, chat_id: None
    database.cleanup_old_sent_illusts = lambda: None
    database.get_schedule_time = lambda chat_id: None
    database.set_schedule_time = lambda chat_id, schedule_time: None
    database.remove_schedule_time = lambda chat_id: None
    database.get_all_schedule_times = lambda: []
    database.get_all_random_ranking_groups = lambda: []
    database.get_random_rankings = lambda chat_id: []
    database.get_random_search_group_config = lambda chat_id: None
    database.add_random_search_send_attempt = lambda **kwargs: None
    database.try_claim_random_search_execution = lambda **kwargs: True
    database.release_random_search_execution = lambda *args, **kwargs: None

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.star"] = astrbot_star
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["astrbot.core.message.message_event_result"] = message_event_result
    sys.modules["apscheduler"] = apscheduler
    sys.modules["apscheduler.schedulers"] = apscheduler_schedulers
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio
    sys.modules["pixivpy3"] = pixivpy3
    sys.modules["utils.database"] = database
    sys.modules["utils.pixiv_utils"] = pixiv_utils


def remove_import_stubs():
    for module_name in (
        "utils.database",
        "utils.pixiv_utils",
        "utils.random_search",
    ):
        sys.modules.pop(module_name, None)
    utils_package = sys.modules.get("utils")
    if utils_package is not None:
        for attr_name in ("database", "pixiv_utils", "random_search"):
            utils_package.__dict__.pop(attr_name, None)


class RandomPushRetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        remove_import_stubs()

    def _make_service(self, random_search):
        service = object.__new__(random_search.RandomSearchService)
        service.client = object()
        service.context = FakeContext()
        return service

    def _make_config(self, random_search, return_count):
        return random_search.FilterConfig(
            r18_mode="过滤 R18",
            ai_filter_mode="显示 AI 作品",
            return_count=return_count,
            show_details=True,
        )

    def _make_pixiv_config(self):
        return types.SimpleNamespace(
            r18_mode="过滤 R18",
            filter_r18g_only=False,
            ai_filter_mode="显示 AI 作品",
            ai_detection_mode="field_or_tag",
            show_filter_result=False,
            single_response_mode=False,
            forward_threshold=False,
            show_details=True,
            return_count=1,
            random_search_min_interval=30,
            random_search_max_interval=240,
            deep_search_depth=3,
            random_search_empty_retry_enabled=True,
            random_search_empty_retry_extra_depth=3,
            random_search_empty_retry_sources=0,
            automatic_push_excluded_tags=[],
        )

    def test_random_filter_config_merges_source_and_automatic_exclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            service.pixiv_config = self._make_pixiv_config()
            service.pixiv_config.automatic_push_excluded_tags = ["ntr", "悪堕ち"]
            service._resolve_group_runtime_config = lambda chat_id: types.SimpleNamespace(
                return_count=1,
                min_likes=0,
            )

            config = service._build_filter_config(
                "random:test",
                ["custom", "ntr"],
                "905956314",
            )

            self.assertEqual(config.excluded_tags, ["custom", "ntr", "悪堕ち"])

    def test_random_ranking_filter_config_uses_automatic_exclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            service.pixiv_config = self._make_pixiv_config()
            service.pixiv_config.automatic_push_excluded_tags = ["ntr", "悪堕ち"]
            service._resolve_group_runtime_config = lambda chat_id: types.SimpleNamespace(
                return_count=1,
                min_likes=0,
            )

            config = service._build_filter_config("random:ranking", [], "905956314")

            self.assertEqual(config.excluded_tags, ["ntr", "悪堕ち"])

    def test_random_retry_return_annotation_matches_delivery_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            hints = typing.get_type_hints(
                random_search.RandomSearchService._send_random_illust_with_retry
            )

            self.assertIs(hints["return"], random_search.RandomIllustDeliveryResult)

    async def _run_with_patches(self, fail_ids, return_count):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            calls = collections.Counter()
            attempts = []
            sent_records = []

            async def fake_send_pixiv_image(
                client, event, illust, detail, show_details, send_all_pages=False
            ):
                calls[illust.id] += 1
                if illust.id in fail_ids:
                    yield event.plain_result("图片下载失败，仅发送信息：\n标题: failed")
                    return
                yield event.plain_result(f"标题: {illust.title}")

            originals = {
                "send_pixiv_image": random_search.send_pixiv_image,
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "add_random_search_send_attempt": (
                    random_search.add_random_search_send_attempt
                ),
                "add_sent_illust": random_search.add_sent_illust,
                "shuffle": random_search.random.shuffle,
                "sleep": random_search.asyncio.sleep,
            }

            random_search.send_pixiv_image = fake_send_pixiv_image
            random_search.filter_illusts_with_reason = (
                lambda illusts, config: (list(illusts), [])
            )
            random_search.add_random_search_send_attempt = (
                lambda **kwargs: attempts.append(kwargs)
            )
            random_search.add_sent_illust = (
                lambda illust_id, chat_id: sent_records.append((illust_id, chat_id))
            )
            random_search.random.shuffle = lambda items: None
            random_search.asyncio.sleep = lambda delay: _noop_async()

            try:
                service = self._make_service(random_search)
                result = await service._send_random_illusts_with_fallback(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="時雨(艦隊これくしょん)",
                    initial_illusts=[FakeIllust(1), FakeIllust(2), FakeIllust(3)],
                    config=self._make_config(random_search, return_count),
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search.asyncio
                        if name == "sleep"
                        else random_search,
                        name,
                        value,
                    )

            return service, result, calls, attempts, sent_records

    async def test_failed_image_is_retried_then_replaced_by_next_candidate(self):
        service, result, calls, attempts, sent_records = await self._run_with_patches(
            fail_ids={1},
            return_count=2,
        )

        self.assertEqual(calls[1], 3)
        self.assertEqual(calls[2], 1)
        self.assertEqual(calls[3], 1)
        self.assertEqual(result.sent_count, 2)
        self.assertEqual(result.sent_image_count, 2)
        self.assertEqual(len(service.context.sent_messages), 2)
        self.assertEqual(sent_records, [(2, "172448191"), (3, "172448191")])
        self.assertTrue(any(not item["success"] and item["illust_id"] == 1 for item in attempts))

        sent_text = "\n".join(
            item.text
            for _, message in service.context.sent_messages
            for item in message.chain
        )
        self.assertNotIn("图片下载失败", sent_text)

    async def test_two_consecutive_failed_candidates_stop_silently(self):
        service, result, calls, attempts, sent_records = await self._run_with_patches(
            fail_ids={1, 2, 3},
            return_count=2,
        )

        self.assertEqual(calls[1], 3)
        self.assertEqual(calls[2], 3)
        self.assertEqual(calls[3], 0)
        self.assertEqual(result.sent_count, 0)
        self.assertEqual(result.sent_image_count, 0)
        self.assertEqual(service.context.sent_messages, [])
        self.assertEqual(sent_records, [])
        self.assertEqual(
            [item["illust_id"] for item in attempts if not item["success"]],
            [1, 2],
        )

    async def test_send_timeout_with_success_eventret_is_treated_as_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            attempts = []
            originals = {
                "add_random_search_send_attempt": (
                    random_search.add_random_search_send_attempt
                ),
                "cleanup_pixiv_temp_files": random_search.cleanup_pixiv_temp_files,
            }
            random_search.add_random_search_send_attempt = (
                lambda **kwargs: attempts.append(kwargs)
            )
            random_search.cleanup_pixiv_temp_files = (
                lambda message_content: _noop_async()
            )

            try:
                service = self._make_service(random_search)
                service.context = FakeTimeoutContext()
                sent_ids = await service._send_message_with_attempt_record(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="時雨(艦隊これくしょん)",
                    message_content=FakeMessageChain().message("标题: timeout"),
                    related_illust_ids=[73092032],
                    success_log="消息已发送",
                    failure_log="发送失败",
                )
            finally:
                for name, value in originals.items():
                    setattr(random_search, name, value)

            self.assertEqual(sent_ids, {73092032})
            self.assertEqual(len(attempts), 1)
            self.assertTrue(attempts[0]["success"])
            self.assertEqual(attempts[0]["illust_id"], 73092032)

    async def test_tag_search_keeps_expanding_until_candidate_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            client = FakeSearchClient(final_page_with_candidates=7)
            sent_records = []
            filter_call_sizes = []

            async def fake_send_pixiv_image(
                client, event, illust, detail, show_details, send_all_pages=False
            ):
                yield event.plain_result(f"标题: {illust.title}")

            def fake_filter_sent_illusts(illusts, chat_id):
                filter_call_sizes.append(len(illusts))
                return [illust for illust in illusts if illust.id >= 7000]

            originals = {
                "send_pixiv_image": random_search.send_pixiv_image,
                "filter_sent_illusts": random_search.filter_sent_illusts,
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "get_random_search_group_config": random_search.get_random_search_group_config,
                "add_random_search_send_attempt": (
                    random_search.add_random_search_send_attempt
                ),
                "add_sent_illust": random_search.add_sent_illust,
                "shuffle": random_search.random.shuffle,
                "sleep": random_search.asyncio.sleep,
            }

            random_search.send_pixiv_image = fake_send_pixiv_image
            random_search.filter_sent_illusts = fake_filter_sent_illusts
            random_search.filter_illusts_with_reason = (
                lambda illusts, config: (list(illusts), [])
            )
            random_search.get_random_search_group_config = lambda chat_id: None
            random_search.add_random_search_send_attempt = lambda **kwargs: None
            random_search.add_sent_illust = (
                lambda illust_id, chat_id: sent_records.append((illust_id, chat_id))
            )
            random_search.random.shuffle = lambda items: None
            random_search.asyncio.sleep = lambda delay: _noop_async()

            try:
                service = object.__new__(random_search.RandomSearchService)
                service.client_wrapper = FakeClientWrapper()
                service.client = client
                service.pixiv_config = self._make_pixiv_config()
                service.context = FakeContext()

                result = await service._execute_tag_search(
                    "172448191",
                    types.SimpleNamespace(
                        tag="時雨(艦隊これくしょん)",
                        session_id="default:GroupMessage:172448191",
                    ),
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search.asyncio
                        if name == "sleep"
                        else random_search,
                        name,
                        value,
                    )

            self.assertEqual(client.calls, [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(
                [call[0] for call in service.client_wrapper.calls],
                ["search_illust"] * 7,
            )
            self.assertEqual(filter_call_sizes, [30] * 7)
            self.assertTrue(result.had_sendable_candidates)
            self.assertEqual(result.sent_count, 1)
            self.assertEqual(len(service.context.sent_messages), 1)
            self.assertEqual(sent_records, [(7000, "172448191")])

    async def test_random_push_empty_result_is_silent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = object.__new__(random_search.RandomSearchService)
            service.context = FakeContext()
            service._empty_retry_enabled = lambda: False
            service._execute_tag_search = lambda chat_id, tag: _result_async(
                random_search.RandomSearchExecutionResult(
                    had_sendable_candidates=False
                )
            )

            originals = {
                "get_random_tags": random_search.get_random_tags,
                "get_random_rankings": random_search.get_random_rankings,
                "choice": random_search.random.choice,
                "add_random_search_send_attempt": (
                    random_search.add_random_search_send_attempt
                ),
            }
            random_search.get_random_tags = lambda chat_id: [
                types.SimpleNamespace(
                    tag="時雨(艦隊これくしょん)",
                    session_id="default:GroupMessage:172448191",
                )
            ]
            random_search.get_random_rankings = lambda chat_id: []
            random_search.random.choice = lambda options: options[0]
            random_search.add_random_search_send_attempt = lambda **kwargs: None

            try:
                sent_count = await service.execute_search_for_group("172448191")
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "choice" else random_search,
                        name,
                        value,
                    )

            self.assertEqual(sent_count, 0)
            self.assertEqual(service.context.sent_messages, [])

    async def test_successful_illust_is_recorded_before_next_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            sent_records = []

            async def fake_send_random_illust_with_retry(
                chat_id,
                session_id,
                source_type,
                source_name,
                illust,
                config,
            ):
                if illust.id == 2:
                    self.assertEqual(sent_records, [(1, "172448191")])
                return random_search.RandomIllustDeliveryResult(
                    illust_ids=frozenset({illust.id}),
                    image_count=1,
                )

            originals = {
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "add_sent_illust": random_search.add_sent_illust,
                "shuffle": random_search.random.shuffle,
            }
            random_search.filter_illusts_with_reason = (
                lambda illusts, config: (list(illusts), [])
            )
            random_search.add_sent_illust = (
                lambda illust_id, chat_id: sent_records.append((illust_id, chat_id))
            )
            random_search.random.shuffle = lambda items: None
            service._send_random_illust_with_retry = fake_send_random_illust_with_retry

            try:
                result = await service._send_random_illusts_with_fallback(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="test",
                    initial_illusts=[FakeIllust(1), FakeIllust(2)],
                    config=self._make_config(random_search, return_count=2),
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search,
                        name,
                        value,
                    )

            self.assertTrue(result.had_sendable_candidates)
            self.assertEqual(result.sent_count, 2)
            self.assertEqual(sent_records, [(1, "172448191"), (2, "172448191")])

    async def test_retry_returns_illust_ids_and_delivered_image_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            send_all_pages_values = []

            async def fake_send_pixiv_image(
                client, event, illust, detail, show_details, send_all_pages=False
            ):
                send_all_pages_values.append(send_all_pages)
                yield event.plain_result(f"title: {illust.title}")

            original = random_search.send_pixiv_image
            random_search.send_pixiv_image = fake_send_pixiv_image
            try:
                result = await service._send_random_illust_with_retry(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="test",
                    illust=FakeIllust(148023016, page_count=2),
                    config=self._make_config(random_search, return_count=3),
                )
            finally:
                random_search.send_pixiv_image = original

            self.assertEqual(result.illust_ids, frozenset({148023016}))
            self.assertEqual(result.image_count, 2)
            self.assertEqual(send_all_pages_values, [True])

    async def test_image_budget_stops_after_delivered_multi_page_illust(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            sent_candidate_ids = []
            sent_records = []

            async def fake_send_random_illust_with_retry(
                chat_id, session_id, source_type, source_name, illust, config
            ):
                sent_candidate_ids.append(illust.id)
                return random_search.RandomIllustDeliveryResult(
                    illust_ids=frozenset({illust.id}),
                    image_count=illust.delivery_image_count,
                )

            originals = {
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "add_sent_illust": random_search.add_sent_illust,
                "shuffle": random_search.random.shuffle,
            }
            random_search.filter_illusts_with_reason = (
                lambda illusts, config: (list(illusts), [])
            )
            random_search.add_sent_illust = (
                lambda illust_id, chat_id: sent_records.append((illust_id, chat_id))
            )
            random_search.random.shuffle = lambda items: None
            service._send_random_illust_with_retry = fake_send_random_illust_with_retry
            try:
                result = await service._send_random_illusts_with_fallback(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="test",
                    initial_illusts=[
                        FakeIllust(1, page_count=1),
                        FakeIllust(2, page_count=5),
                        FakeIllust(3, page_count=1),
                    ],
                    config=self._make_config(random_search, return_count=3),
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search,
                        name,
                        value,
                    )

            self.assertEqual(sent_candidate_ids, [1, 2])
            self.assertEqual(result.sent_count, 2)
            self.assertEqual(result.sent_image_count, 6)
            self.assertEqual(sent_records, [(1, "172448191"), (2, "172448191")])
            self.assertNotIn((3, "172448191"), sent_records)

    async def test_ten_page_illust_counts_as_three_delivered_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)

            async def fake_send_random_illust_with_retry(
                chat_id, session_id, source_type, source_name, illust, config
            ):
                return random_search.RandomIllustDeliveryResult(
                    illust_ids=frozenset({illust.id}),
                    image_count=illust.delivery_image_count,
                )

            originals = {
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "shuffle": random_search.random.shuffle,
            }
            random_search.filter_illusts_with_reason = (
                lambda illusts, config: (list(illusts), [])
            )
            random_search.random.shuffle = lambda items: None
            service._send_random_illust_with_retry = fake_send_random_illust_with_retry
            try:
                result = await service._send_random_illusts_with_fallback(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="test",
                    initial_illusts=[FakeIllust(10, page_count=10)],
                    config=self._make_config(random_search, return_count=3),
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search,
                        name,
                        value,
                    )

            self.assertEqual(result.sent_count, 1)
            self.assertEqual(result.sent_image_count, 3)

    async def test_automatic_excluded_candidates_are_never_sent_or_cached(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            service.pixiv_config = self._make_pixiv_config()
            service.pixiv_config.automatic_push_excluded_tags = ["excluded"]
            service._resolve_group_runtime_config = lambda chat_id: types.SimpleNamespace(
                return_count=2,
                min_likes=0,
            )
            config = service._build_filter_config("random:test", [], "172448191")
            sent_candidate_ids = []
            sent_records = []

            async def fake_send_random_illust_with_retry(
                chat_id, session_id, source_type, source_name, illust, config
            ):
                sent_candidate_ids.append(illust.id)
                return random_search.RandomIllustDeliveryResult(
                    illust_ids=frozenset({illust.id}),
                    image_count=1,
                )

            originals = {
                "add_sent_illust": random_search.add_sent_illust,
                "shuffle": random_search.random.shuffle,
            }
            random_search.add_sent_illust = (
                lambda illust_id, chat_id: sent_records.append((illust_id, chat_id))
            )
            random_search.random.shuffle = lambda items: None
            service._send_random_illust_with_retry = fake_send_random_illust_with_retry
            try:
                result = await service._send_random_illusts_with_fallback(
                    chat_id="172448191",
                    session_id="default:GroupMessage:172448191",
                    source_type="tag",
                    source_name="test",
                    initial_illusts=[
                        FakeIllust(1, tags=["automatic-excluded"]),
                        FakeIllust(2, tags=["allowed"]),
                    ],
                    config=config,
                )
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.random if name == "shuffle" else random_search,
                        name,
                        value,
                    )

            self.assertTrue(result.had_sendable_candidates)
            self.assertEqual(sent_candidate_ids, [2])
            self.assertEqual(sent_records, [(2, "172448191")])
            self.assertNotIn((1, "172448191"), sent_records)


async def _noop_async():
    return None


async def _result_async(result):
    return result


if __name__ == "__main__":
    unittest.main()
