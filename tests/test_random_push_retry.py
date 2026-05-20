import collections
import sys
import tempfile
import types
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


class FakeUser:
    name = "artist"


class FakeIllust:
    def __init__(self, illust_id):
        self.id = illust_id
        self.title = f"title-{illust_id}"
        self.user = FakeUser()
        self.tags = []
        self.type = "illust"


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

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.star"] = astrbot_star
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["astrbot.core.message.message_event_result"] = message_event_result
    sys.modules["apscheduler"] = apscheduler
    sys.modules["apscheduler.schedulers"] = apscheduler_schedulers
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio
    sys.modules["pixivpy3"] = pixivpy3


class RandomPushRetryTest(unittest.IsolatedAsyncioTestCase):
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

    async def _run_with_patches(self, fail_ids, return_count):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            calls = collections.Counter()
            attempts = []
            sent_records = []

            async def fake_send_pixiv_image(client, event, illust, detail, show_details):
                calls[illust.id] += 1
                if illust.id in fail_ids:
                    yield event.plain_result("图片下载失败，仅发送信息：\n标题: failed")
                    return
                yield event.plain_result(f"标题: {illust.title}")

            originals = {
                "send_pixiv_image": random_search.send_pixiv_image,
                "filter_illusts_with_reason": random_search.filter_illusts_with_reason,
                "add_random_search_send_attempt": random_search.add_random_search_send_attempt,
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
        self.assertEqual(service.context.sent_messages, [])
        self.assertEqual(sent_records, [])
        self.assertEqual(
            [item["illust_id"] for item in attempts if not item["success"]],
            [1, 2],
        )


async def _noop_async():
    return None


if __name__ == "__main__":
    unittest.main()
