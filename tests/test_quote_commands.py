import importlib
import sys
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "quote_test_plugin"


class Reply:
    def __init__(self, id=77, **kwargs):
        self.id = id
        self.__dict__.update(kwargs)


def module(name, **kwargs):
    result = types.ModuleType(name)
    result.__dict__.update(kwargs)
    return result


def payload(text="作品ID: 123"):
    return {
        "message_id": 77, "message_type": "group", "group_id": 100,
        "sender": {"user_id": 999},
        "message": [
            {"type": "image", "data": {"file": "bot-image.jpg"}},
            {"type": "text", "data": {"text": text}},
        ],
    }


class Event:
    def __init__(self, response=None):
        self.messages = [Reply(chain=[{"forged": "作品ID: 456"}])]
        self.bot = types.SimpleNamespace(call_action=AsyncMock(return_value=payload() if response is None else response))
        self.unified_msg_origin = "qq:GroupMessage:100"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "100"

    def get_self_id(self):
        return "999"

    def get_messages(self):
        return self.messages

    def plain_result(self, text):
        return text

    def chain_result(self, components):
        return types.SimpleNamespace(chain=components)


def illust(id=123, artist=8, **kwargs):
    data = dict(id=id, title="work", user=types.SimpleNamespace(id=artist, name="API author"),
                tags=[], x_restrict=0, illust_ai_type=0)
    data.update(kwargs)
    return types.SimpleNamespace(**data)


class QuoteCommandsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stubs = {
            PACKAGE: module(PACKAGE, __path__=[str(ROOT)]),
            f"{PACKAGE}.handlers": module(f"{PACKAGE}.handlers", __path__=[str(ROOT / "handlers")]),
            f"{PACKAGE}.utils": module(f"{PACKAGE}.utils", __path__=[str(ROOT / "utils")]),
            "astrbot": module("astrbot", __path__=[]),
            "astrbot.api": module("astrbot.api", logger=Mock(), __path__=[]),
            "astrbot.api.message_components": module("astrbot.api.message_components", Reply=Reply),
            f"{PACKAGE}.utils.pixiv_utils": module(f"{PACKAGE}.utils.pixiv_utils",
                send_pixiv_image=Mock(), send_forward_message=Mock(),
                cleanup_pixiv_temp_files=AsyncMock()),
        }
        self.modules = patch.dict(sys.modules, stubs)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.quote = importlib.import_module(f"{PACKAGE}.handlers.quote")
        config_module = importlib.import_module(f"{PACKAGE}.utils.config")
        self.config = config_module.PixivConfig({"return_count": 2, "single_response_mode": False})
        self.api = types.SimpleNamespace(illust_detail=object(), user_illusts=object())
        self.wrapper = types.SimpleNamespace(client_api=self.api,
            authenticate=AsyncMock(return_value=True), call_pixiv_api=AsyncMock())
        self.handler = self.quote.QuoteHandler(self.wrapper, self.config)

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.guard = importlib.import_module(f"{PACKAGE}.utils.delivery_guard")
        self.ledger = self.guard.DeliveryLedger(Path(self.temp.name) / "delivery.db")
        self.sent = []

        class Field:
            def __eq__(self, other):
                return self

            def __and__(self, other):
                return self

            def in_(self, values):
                return self

        test = self

        class Sent:
            chat_id, illust_id = Field(), Field()

            @classmethod
            def select(cls, *args):
                return cls

            @classmethod
            def where(cls, condition):
                return [types.SimpleNamespace(illust_id=value) for value in test.sent]

        sys.modules[f"{PACKAGE}.utils.database"] = module(
            f"{PACKAGE}.utils.database", SentIllust=Sent,
            add_sent_illust=lambda id, chat: self.sent.append(id),
        )

        async def status(session, ids):
            return await self.guard.delivery_status(session, ids, ledger=self.ledger)

        async def send(session, ids, transport, **kwargs):
            return await self.guard.guarded_send(
                session, ids, transport, ledger=self.ledger, **kwargs
            )

        for name, replacement in (("delivery_status", status), ("guarded_send", send)):
            patcher = patch.object(self.quote, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def image(client, event, item, detail, **kwargs):
            yield event.chain_result([types.SimpleNamespace(type="Image", id=item.id)])

        self.forward_calls = []

        async def forward(client, event, items, builder, **kwargs):
            self.forward_calls.append((items, kwargs))
            yield event.chain_result([types.SimpleNamespace(type="Nodes", nodes=[
                types.SimpleNamespace(type="Node", content=[types.SimpleNamespace(type="Image", id=item.id)])
                for item in items
            ])])

        for name, replacement in (("send_pixiv_image", image), ("send_forward_message", forward)):
            patcher = patch.object(self.quote, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_recovery_refetches_and_ignores_untrusted_reply_chain(self):
        event = Event()
        self.assertEqual(await self.quote.recover_quoted_illust_id(event), 123)
        event.bot.call_action.assert_awaited_once_with(action="get_msg", message_id=77)
        event.messages[0].sender_id = "888"
        self.assertEqual(await self.quote.recover_quoted_illust_id(event), 123)

    async def test_only_exact_generated_identifier_lines_with_image_are_accepted(self):
        for text in ("作品ID: 123", "标题: a\n链接: https://www.pixiv.net/artworks/123",
                     "https://www.pixiv.net/artworks/123", "作品ID: 123\n链接: https://www.pixiv.net/artworks/123"):
            with self.subTest(text=text):
                self.assertEqual(await self.quote.recover_quoted_illust_id(Event(payload(text))), 123)
        for text in ("look 作品ID: 123", "作品ID: 123 extra", "作者ID: 123", "123",
                     "链接: https://www.pixiv.net.evil.test/artworks/123",
                     "链接: https://www.pixiv.net/artworks/123?fake=1"):
            with self.subTest(text=text), self.assertRaises(self.quote.QuoteError):
                await self.quote.recover_quoted_illust_id(Event(payload(text)))
        no_image = payload()
        no_image["message"] = no_image["message"][1:]
        with self.assertRaises(self.quote.QuoteError):
            await self.quote.recover_quoted_illust_id(Event(no_image))

    async def test_forged_cross_group_mismatched_and_malformed_responses_are_rejected(self):
        for key, value in (("sender", {"user_id": 888}), ("sender", None),
                           ("group_id", 101), ("group_id", None),
                           ("message_type", "private"), ("message_id", 78),
                           ("message_id", None), ("message", "[CQ:image,file=x]作品ID: 123"),
                           ("message", [{"type": "text", "data": None}])):
            response = payload()
            response[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(self.quote.QuoteError):
                await self.quote.recover_quoted_illust_id(Event(response))
        for response in ([], {"data": payload()}, {"retcode": 1}, "bad"):
            with self.subTest(response=response), self.assertRaises(self.quote.QuoteError):
                await self.quote.recover_quoted_illust_id(Event(response))

    async def test_ambiguity_and_nested_forward_are_actionable_rejections(self):
        responses = [payload("作品ID: 123\n作品ID: 456")]
        for kind in ("reply", "forward", "node"):
            response = payload()
            response["message"].append({"type": kind, "data": {"id": "456"}})
            responses.append(response)
        for response in responses:
            with self.subTest(response=response), self.assertRaisesRegex(self.quote.QuoteError, "单.*图片|单.*作品"):
                await self.quote.recover_quoted_illust_id(Event(response))

    async def test_missing_reply_unsupported_and_failed_get_msg_fail_closed(self):
        for messages in ([], [Reply(), Reply()], [Reply("bad")]):
            event = Event()
            event.messages = messages
            with self.subTest(messages=messages), self.assertRaises(self.quote.QuoteError):
                await self.quote.recover_quoted_illust_id(event)
            event.bot.call_action.assert_not_awaited()
        event = Event()
        event.get_platform_name = lambda: "other"
        with self.assertRaisesRegex(self.quote.QuoteError, "OneBot"):
            await self.quote.recover_quoted_illust_id(event)
        event = Event()
        event.bot.call_action.side_effect = RuntimeError("private diagnostic")
        with self.assertRaises(self.quote.QuoteError) as error:
            await self.quote.recover_quoted_illust_id(event)
        self.assertNotIn("private diagnostic", str(error.exception))

    async def test_resolve_author_from_api_not_quoted_name(self):
        self.wrapper.call_pixiv_api.return_value = types.SimpleNamespace(illust=illust())
        resolved = await self.handler.resolve(Event(payload("作品ID: 123\n作者: forged")))
        self.assertEqual(resolved.user.id, 8)
        self.wrapper.call_pixiv_api.assert_awaited_once_with(self.api.illust_detail, 123)

    async def test_bad_detail_does_not_resolve_another_artwork_or_invalid_artist(self):
        for result in (None, types.SimpleNamespace(illust=illust(456)),
                       types.SimpleNamespace(illust=illust(artist=0))):
            self.wrapper.call_pixiv_api.return_value = result
            with self.subTest(result=result), self.assertRaises(self.quote.QuoteError):
                await self.handler.resolve(Event())

    async def more(self, items, *, count=3, event=None):
        self.wrapper.call_pixiv_api.return_value = types.SimpleNamespace(illusts=items, next_url=None)
        event = event or Event()
        if not hasattr(event, "send"):
            event.send = AsyncMock(return_value=None)
        results = [result async for result in self.handler.more(event, illust(), count)]
        return event, results

    async def test_more_excludes_quote_duplicate_wrong_artist_and_sent_pending_unknown(self):
        event = Event()
        self.sent.append(2)
        for id, state in ((3, "pending"), (4, "unknown"), (5, "sent")):
            token, _ = self.ledger.begin(event.unified_msg_origin, [id])
            if state != "pending":
                self.ledger.finish(event.unified_msg_origin, [id], token, state)
        event, _ = await self.more([
            illust(), illust(1), illust(1), illust(2), illust(3), illust(4), illust(5), illust(6, artist=9),
        ], event=event)
        self.assertEqual([call.args[0].chain[0].id for call in event.send.await_args_list], [1])
        self.assertEqual(self.sent, [2, 1])
        self.wrapper.call_pixiv_api.assert_awaited_once_with(self.api.user_illusts, 8)

    async def test_subscription_sent_during_image_preparation_blocks_quote_transport(self):
        for single_response in (False, True):
            with self.subTest(single_response=single_response):
                self.sent.clear()
                self.config.single_response_mode = single_response
                event = Event()
                event.unified_msg_origin = f"quote-race-{single_response}:GroupMessage:100"
                event.send = AsyncMock(return_value=None)
                subscription_send = AsyncMock(return_value={"message_id": "subscription-1"})
                recorded = {}
                prefilter = AsyncMock(wraps=self.handler._exclude_unavailable)

                async def prepare(client, current_event, item_or_items, *args, **kwargs):
                    prefilter.assert_awaited_once()
                    self.assertEqual([item.id for item in prefilter.await_args.args[1]], [1])
                    outcome = await self.guard.guarded_send(
                        current_event.unified_msg_origin, [1], subscription_send,
                        ledger=self.ledger,
                    )
                    self.assertEqual(outcome.status, "sent")
                    recorded.update(self.ledger.status(current_event.unified_msg_origin, [1]))
                    yield current_event.chain_result([
                        types.SimpleNamespace(type="Image", id=1)
                    ])

                with patch.object(self.handler, "_exclude_unavailable", prefilter), \
                     patch.object(self.quote, "send_pixiv_image", prepare), \
                     patch.object(self.quote, "send_forward_message", prepare):
                    _, results = await self.more([illust(1)], count=1, event=event)

                subscription_send.assert_awaited_once()
                event.send.assert_not_awaited()
                self.assertEqual(self.sent, [])
                self.assertEqual(self.ledger.status(event.unified_msg_origin, [1]), recorded)
                self.assertEqual(recorded[1]["state"], "sent")
                self.assertEqual(recorded[1]["receipt_id"], "subscription-1")
                self.assertTrue(any("停止追加" in result for result in results))

    async def test_more_preserves_all_filters_and_never_mutates_global_count(self):
        self.config.r18_mode = "允许 R18"
        self.config.filter_r18g_only = True
        self.config.min_bookmarks = 100
        self.config.min_views = 100
        self.config.min_likes = 10
        self.config.automatic_push_excluded_tags = ["ntr"]
        base = dict(total_bookmarks=200, total_view=200, like_count=20)
        items = [illust(1, **base)]
        for id, overrides in (
            (2, {"tags": [types.SimpleNamespace(name="R-18G")]}),
            (3, {"illust_ai_type": 2}),
            (4, {"tags": [types.SimpleNamespace(name="NTR")]}),
            (5, {"total_bookmarks": 0}), (6, {"total_view": 0}), (7, {"like_count": 0}),
        ):
            items.append(illust(id, **(base | overrides)))
        event, _ = await self.more(items, count=10)
        self.assertEqual([call.args[0].chain[0].id for call in event.send.await_args_list], [1])
        self.assertEqual(self.config.return_count, 2)
        self.assertEqual(self.config.config["return_count"], 2)
        self.config.r18_mode = "过滤 R18"
        event, _ = await self.more([illust(8, **(base | {"x_restrict": 1}))])
        event.send.assert_not_awaited()

    async def test_more_default_and_max_counts_use_the_existing_delivery_pipeline(self):
        for count in (1, 3, 10):
            with self.subTest(count=count):
                event = Event()
                event.unified_msg_origin = f"qq:GroupMessage:{100 + count}"
                self.sent.clear()
                event, _ = await self.more([illust(id) for id in range(1, 12)], count=count, event=event)
                self.assertEqual(event.send.await_count, count)
                self.assertEqual(len(self.sent), count)
        for count in (0, 11, True, None):
            with self.subTest(count=count), self.assertRaises(self.quote.QuoteError):
                await self.more([illust(1)], count=count)

    async def test_single_response_and_custom_forward_policy_are_preserved(self):
        for single, forward in ((True, False), (False, True)):
            self.config.single_response_mode = single
            self.config.forward_threshold = forward
            self.config.show_details = False
            event = Event()
            event.unified_msg_origin = f"qq{single}:GroupMessage:100"
            self.sent.clear()
            event, _ = await self.more([illust(1, page_count=3), illust(2)], count=2, event=event)
            event.send.assert_awaited_once()
            self.assertEqual(set(self.sent), {1, 2})
            self.assertEqual(set(item.id for item in self.forward_calls[-1][0]), {1, 2})
            self.assertEqual(self.forward_calls[-1][1].get("single_batch", False), single)

    async def test_unknown_or_blocked_stops_without_fallback_or_sent_cache(self):
        event = Event()
        event.send = AsyncMock(side_effect=TimeoutError("adapter timeout"))
        event, results = await self.more([illust(1), illust(2)], count=2, event=event)
        event.send.assert_awaited_once()
        self.assertEqual(self.sent, [])
        self.assertIn("待核实", results[-1])
        rows = self.ledger.status(event.unified_msg_origin, [1, 2])
        self.assertEqual([row["state"] for row in rows.values()], ["unknown"])
        with patch.object(self.quote, "guarded_send", AsyncMock(return_value=self.guard.DeliveryOutcome("blocked"))):
            event = Event()
            event, results = await self.more([illust(3), illust(4)], count=2, event=event)
            event.send.assert_not_awaited()
            self.assertIn("停止追加", results[-1])

    async def test_state_lookup_failure_does_not_send(self):
        event = Event()
        event.send = AsyncMock()
        with patch.object(self.quote, "delivery_status", AsyncMock(side_effect=OSError("unreadable"))):
            with self.assertRaises(OSError):
                await self.more([illust(1)], event=event)
        event.send.assert_not_awaited()

    async def test_ledger_begin_failure_reports_no_transport_not_unknown(self):
        outcome = self.guard.DeliveryOutcome("blocked", error=OSError("write failed"))
        with patch.object(self.quote, "guarded_send", AsyncMock(return_value=outcome)):
            event, results = await self.more([illust(1), illust(2)], count=2)
        event.send.assert_not_awaited()
        self.assertIn("未开始发送", results[-1])
        self.assertNotIn("待核实", results[-1])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.status(event.unified_msg_origin, [1, 2]), {})

    async def test_explicit_transport_failure_is_not_sent_and_can_continue(self):
        event = Event()
        event.send = AsyncMock(side_effect=[False, None])
        event, results = await self.more([illust(1), illust(2)], count=2, event=event)
        self.assertEqual(event.send.await_count, 2)
        self.assertEqual(len(self.sent), 1)
        states = self.ledger.status(event.unified_msg_origin, [1, 2])
        self.assertEqual(sorted(row["state"] for row in states.values()), ["failed", "sent"])
        self.assertEqual(states[self.sent[0]]["state"], "sent")
        self.assertTrue(any("发送失败" in result for result in results))

    async def test_text_only_pipeline_result_never_enters_transport_or_sent_history(self):
        async def text_only(client, event, item, detail, **kwargs):
            yield event.chain_result([types.SimpleNamespace(type="Plain", text="仅作品信息")])

        with patch.object(self.quote, "send_pixiv_image", text_only):
            event, results = await self.more([illust(1)], count=1)
        event.send.assert_not_awaited()
        self.assertEqual(self.sent, [])
        self.assertIn("未发送", results[-1])

    async def test_partial_forward_image_failure_never_records_other_ids_as_sent(self):
        self.config.single_response_mode = True

        async def failed_forward(client, event, items, builder, **kwargs):
            yield event.chain_result([types.SimpleNamespace(type="Nodes", nodes=[
                types.SimpleNamespace(type="Node", content=[types.SimpleNamespace(type="Image", id=1)]),
                types.SimpleNamespace(type="Node", content=[types.SimpleNamespace(type="Plain", text="图片下载失败，仅发送信息")]),
            ])])

        with patch.object(self.quote, "send_forward_message", failed_forward):
            event, results = await self.more([illust(1), illust(2)], count=2)
        event.send.assert_not_awaited()
        self.assertEqual(self.sent, [])
        self.assertIn("未发送", results[-1])
        self.assertEqual(self.ledger.status(event.unified_msg_origin, [1, 2]), {})
        self.quote.cleanup_pixiv_temp_files.assert_awaited_once()

    async def test_pagination_is_bounded_and_keeps_same_artist(self):
        self.wrapper.call_pixiv_api.side_effect = [
            types.SimpleNamespace(illusts=[illust(), illust(1)], next_url="https://app-api.pixiv.net/v1/user/illusts?user_id=999&offset=30"),
            types.SimpleNamespace(illusts=[illust(2), illust(3)], next_url=None),
        ]
        event = Event()
        event.send = AsyncMock(return_value=None)
        _ = [result async for result in self.handler.more(event, illust())]
        self.assertEqual(event.send.await_count, 3)
        self.wrapper.call_pixiv_api.assert_any_await(self.api.user_illusts, 8, offset=30)


if __name__ == "__main__":
    unittest.main()
