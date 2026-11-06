import asyncio
import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "chat_commands_test_plugin"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def import_stubs():
    stubs = {
        PACKAGE: module(PACKAGE, __path__=[str(ROOT)]),
        f"{PACKAGE}.utils": module(
            f"{PACKAGE}.utils", __path__=[str(ROOT / "utils")]
        ),
        f"{PACKAGE}.handlers": module(
            f"{PACKAGE}.handlers", __path__=[str(ROOT / "handlers")]
        ),
        f"{PACKAGE}.core": module(
            f"{PACKAGE}.core", __path__=[str(ROOT / "core")]
        ),
        "astrbot": module("astrbot", __path__=[]),
        "astrbot.api": module("astrbot.api", logger=Mock(), __path__=[]),
        "astrbot.api.event": module("astrbot.api.event", AstrMessageEvent=object),
        "astrbot.api.star": module(
            "astrbot.api.star", Context=object, Star=object, StarTools=object
        ),
        "astrbot.api.all": module(
            "astrbot.api.all", command=lambda name: lambda function: function,
            regex=lambda pattern: lambda function: function,
        ),
        "astrbot.api.message_components": module(
            "astrbot.api.message_components", Reply=type("Reply", (), {})
        ),
        "aiohttp": module("aiohttp"),
    }
    for name, attributes in {
        "utils.database": {"initialize_database": Mock()},
        "utils.subscription": {"SubscriptionService": object},
        "utils.pixiv_utils": {
            "init_pixiv_utils": Mock(),
            "send_pixiv_image": Mock(),
            "send_forward_message": Mock(),
            "cleanup_pixiv_temp_files": AsyncMock(),
        },
        "utils.help": {
            "init_help_manager": Mock(),
            "get_help_message": lambda *args: "legacy help",
        },
        "utils.llm_tool": {
            "bind_tools_to_plugin_module": Mock(),
            "create_pixiv_llm_tools": Mock(),
        },
        "core.client": {"PixivClientWrapper": object},
        "handlers.user": {"UserHandler": object},
        "handlers.novel": {"NovelHandler": object},
        "handlers.subscribe": {"SubscribeHandler": object},
        "handlers.random_illust": {"RandomIllustHandler": object},
        "handlers.misc": {"MiscHandler": object},
        "handlers.fanbox": {"FanboxHandler": object},
    }.items():
        full_name = f"{PACKAGE}.{name}"
        stubs[full_name] = module(full_name, **attributes)
    return stubs


class FakeEvent:
    def __init__(self, message):
        self.message = message

    def get_message_str(self):
        return self.message

    def plain_result(self, text):
        return text

    def is_admin(self):
        return False

    def get_group_id(self):
        return "100"

    def get_sender_id(self):
        return "2"

    async def get_group(self):
        return types.SimpleNamespace(group_owner="1", group_admins=[])


class ChatCommandsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, import_stubs())
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.commands = importlib.import_module(f"{PACKAGE}.utils.chat_commands")
        self.main = importlib.import_module(f"{PACKAGE}.main")
        self.illust = importlib.import_module(f"{PACKAGE}.handlers.illust")
        config_module = importlib.import_module(f"{PACKAGE}.utils.config")
        self.plugin = object.__new__(self.main.PixivSearchPlugin)
        self.plugin.pixiv_config = config_module.PixivConfig({"return_count": 2})
        self.api_calls = []
        self.deliveries = []

        async def authenticate():
            return True

        async def call_api(function, tags, **kwargs):
            self.api_calls.append(tags)
            await asyncio.sleep(0)
            return types.SimpleNamespace(illusts=[object()] * 10)

        async def deliver(illusts, config, client, event, *args, **kwargs):
            await asyncio.sleep(0)
            self.deliveries.append(config)
            yield (config.display_tag_str, config.return_count)

        self.plugin.client_wrapper = types.SimpleNamespace(
            client_api=types.SimpleNamespace(search_illust=object()),
            authenticate=authenticate,
            call_pixiv_api=call_api,
        )
        self.plugin.illust_handler = self.main.IllustHandler(
            self.plugin.client_wrapper, self.plugin.pixiv_config
        )
        self.delivery_patch = patch.object(
            self.illust, "process_and_send_illusts", deliver
        )
        self.delivery_patch.start()
        self.addCleanup(self.delivery_patch.stop)

    async def route(self, tail, passed=None, prefix="pixiv"):
        # AstrBot supplies only the first space-separated token for a str default.
        if passed is None:
            passed = tail.split()[0] if tail.split() else ""
        event = FakeEvent(f"{prefix} {tail}")
        return [
            result
            async for result in self.plugin.pixiv_search_illust(event, passed)
        ]

    def test_parser_only_dispatches_explicit_first_tokens(self):
        parse = self.commands.parse_chat_command
        for tail in ("", "unknown tags", "help", "help more", "订阅风景 3张",
                     "foo 暂停推送", "搜图片 初音", "landscape  3张"):
            with self.subTest(tail=tail):
                parsed = parse(tail)
                self.assertEqual(parsed.action, "legacy")
                self.assertEqual(parsed.argument, tail)
                self.assertIsNone(parsed.count)
        parsed = parse("搜图 初音ミク,white dress,-ntr 3张")
        self.assertEqual(parsed.action, "search")
        self.assertEqual(parsed.argument, "初音ミク,white dress,-ntr")
        self.assertEqual(parsed.count, 3)

    def test_parser_accepts_artist_ids_and_profile_links(self):
        for artist in (
            "123", "https://www.pixiv.net/users/123",
            "https://www.pixiv.net/en/users/123/?foo=bar#works",
            "http://pixiv.net/member.php?id=123",
            "www.pixiv.net/users/123",
        ):
            for alias, action in (("订阅", "subscribe_add"),
                                  ("取消订阅", "subscribe_remove")):
                with self.subTest(alias=alias, artist=artist):
                    parsed = self.commands.parse_chat_command(f"{alias} {artist}")
                    self.assertEqual((parsed.action, parsed.argument), (action, "123"))

    def test_quote_commands_and_request_only_counts(self):
        parse = self.commands.parse_chat_command
        for tail, action, count in (
            ("查询作者", "quote_author", None),
            ("订阅画师", "quote_subscribe", None),
            ("再来几张", "quote_more", 3),
            ("再来几张 1", "quote_more", 1),
            ("再来几张 10张", "quote_more", 10),
        ):
            with self.subTest(tail=tail):
                request = parse(tail)
                self.assertEqual((request.action, request.count), (action, count))
                self.assertEqual(request.argument, "")
        for tail in ("查询作者 123", "订阅画师 123", "再来几张 0", "再来几张 11张",
                     "再来几张 -1", "再来几张 1.5", "再来几张 2 3", "再来几张 abc"):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                parse(tail)
        for tail in ("查询作者风景", "订阅画师风景", "再来几张风景", "foo 查询作者"):
            self.assertEqual(parse(tail).action, "legacy")

    def test_delivery_reconciliation_commands_are_strict(self):
        parse = self.commands.parse_chat_command
        self.assertEqual(parse("待核实").action, "delivery_status")
        for alias, action in (("确认送达", "delivery_confirm"), ("允许重发", "delivery_retry")):
            for value in ("1", "123", str(2**63 - 1)):
                with self.subTest(alias=alias, value=value):
                    self.assertEqual(parse(f"{alias} {value}"), self.commands.ChatCommand(action, value))
            for value in ("", "0", "-1", "True", "1.5", "123 extra", str(2**63), "9" * 100):
                with self.subTest(alias=alias, value=value), self.assertRaises(ValueError):
                    parse(f"{alias} {value}")
        with self.assertRaises(ValueError):
            parse("待核实 extra")

    def test_parser_rejects_malformed_commands(self):
        invalid = (
            "搜图", "搜图 3张", "搜图 初音 0张", "搜图 初音 11张",
            "搜图 初音 -1张", "搜图 初音 1.5张", "搜图 初音 +2张",
            "订阅", "取消订阅", "订阅 abc", "订阅 0", "订阅 123 456",
            "订阅 https://evil.test/users/123",
            "订阅 https://pixiv.net.evil.test/users/123",
            "订阅 https://www.pixiv.net/artworks/123",
            "订阅 https://www.pixiv.net/users/abc",
            "订阅 https://www.pixiv.net/member.php?id=123&id=456",
            "订阅列表 extra", "暂停推送 extra", "恢复推送 extra",
            "推送状态 extra", "帮助 extra",
        )
        for tail in invalid:
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                self.commands.parse_chat_command(tail)

    async def test_full_tail_survives_astrbot_space_splitting(self):
        for prefix in ("pixiv", "/pixiv"):
            with self.subTest(prefix=prefix):
                result = await self.route("搜图 white dress,初音,-ntr 3张", prefix=prefix)
                self.assertEqual(result, [("white dress,初音,-ntr", 3)])
                self.assertEqual(self.api_calls[-1], "white dress 初音")
                self.assertEqual(self.deliveries[-1].excluded_tags, ["ntr"])

    async def test_legacy_and_unknown_tails_are_not_reinterpreted(self):
        received = []

        async def search(event, tags):
            received.append(tags)
            yield tags

        self.plugin.illust_handler.pixiv_search_illust = search
        for tail in ("white  dress,初音 3张", "unknown action", "订阅风景",
                     "foo 帮助", "help", "help more", ""):
            with self.subTest(tail=tail):
                self.assertEqual(await self.route(tail), [tail])
        self.assertEqual(len(received), 7)

    async def test_local_astrbot_command_filter_full_tail(self):
        source = ROOT.parent / "AstrBot/astrbot/core/star/filter/command.py"
        if not source.is_file():
            self.skipTest("Adjacent AstrBot source is not available")
        prefix = "chat_commands_core_test"
        stubs = {
            "astrbot.core.config": module("astrbot.core.config", AstrBotConfig=object),
            "astrbot.core.platform.astr_message_event": module(
                "astrbot.core.platform.astr_message_event", AstrMessageEvent=object
            ),
            prefix: module(prefix, __path__=[]),
            f"{prefix}.filter": module(
                f"{prefix}.filter", HandlerFilter=object, __path__=[]
            ),
            f"{prefix}.star_handler": module(
                f"{prefix}.star_handler", StarHandlerMetadata=object
            ),
            f"{prefix}.filter.custom_filter": module(
                f"{prefix}.filter.custom_filter", CustomFilter=object
            ),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location(f"{prefix}.filter.command", source)
            core = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(core)
            command_filter = core.CommandFilter(
                "pixiv", handler_md=types.SimpleNamespace(
                    handler=self.main.PixivSearchPlugin.pixiv_search_illust
                )
            )
            for tail, expected in (
                ("搜图 white  dress 3张", ("white  dress", 3)),
                ("white  dress 3张", ("white  dress 3张", 2)),
            ):
                with self.subTest(tail=tail):
                    event = FakeEvent(f"pixiv {tail}")
                    event.is_at_or_wake_command = True
                    extras = {}
                    event.set_extra = extras.__setitem__
                    self.assertTrue(command_filter.filter(event, {}))
                    params = extras["parsed_params"]
                    self.assertEqual(params["tags"], tail.split()[0])
                    result = [item async for item in self.plugin.pixiv_search_illust(
                        event, **params
                    )]
                    self.assertEqual(result, [expected])

    async def test_search_without_count_keeps_default_and_accepts_tag_ending_zhang(self):
        self.assertEqual(await self.route("搜图 纸张"), [("纸张", 2)])
        self.assertEqual(await self.route("搜图 white dress"), [("white dress", 2)])

    async def test_management_routes_through_plugin_methods(self):
        cases = (
            ("订阅 https://www.pixiv.net/users/123", "pixiv_subscribe_add", ("123",)),
            ("取消订阅 123", "pixiv_subscribe_remove", ("123",)),
            ("订阅列表", "pixiv_subscribe_list", ()),
            ("暂停推送", "pixiv_random_suspend", ()),
            ("恢复推送", "pixiv_random_resume", ()),
            ("推送状态", "pixiv_random_status", ()),
        )
        for tail, method, arguments in cases:
            calls = []

            async def guarded(event, *args):
                calls.append(args)
                yield "management guard reached"

            with self.subTest(tail=tail), patch.object(self.plugin, method, guarded):
                self.assertEqual(await self.route(tail), ["management guard reached"])
                self.assertEqual(calls, [arguments])

    async def test_short_and_legacy_subscription_share_actual_permission_guard(self):
        calls = []
        async def add(event, value):
            calls.append(value)
            yield "added"
        self.plugin.subscribe_handler = types.SimpleNamespace(pixiv_subscribe_add=add)
        denied = await self.route("订阅 123")
        self.assertEqual(calls, [])
        self.assertIn("管理员", denied[0])
        event = FakeEvent("pixiv_subscribe_add 123")
        self.assertIn("管理员", [r async for r in self.plugin.pixiv_subscribe_add(event, "123")][0])
        event.is_admin = lambda: True
        self.assertEqual([r async for r in self.plugin.pixiv_subscribe_add(event, "123")], ["added"])
        event.message = "pixiv 订阅 456"
        self.assertEqual([r async for r in self.plugin.pixiv_search_illust(event, "订阅")], ["added"])
        self.assertEqual(calls, ["123", "456"])

    async def test_quote_routes_author_subscription_and_more_without_tag_search(self):
        work = types.SimpleNamespace(id=123, user=types.SimpleNamespace(id=8, name="API author"))
        calls = []

        async def more(handler, event, resolved, count):
            calls.append((resolved.id, count))
            yield "more delivered through guarded pipeline"

        async def subscribe(event, artist):
            calls.append(artist)
            yield "subscribed"

        self.plugin.subscribe_handler = types.SimpleNamespace(pixiv_subscribe_add=subscribe)
        with patch.object(self.main.QuoteHandler, "resolve", AsyncMock(return_value=work)) as resolve, \
             patch.object(self.main.QuoteHandler, "more", more):
            result = await self.route("查询作者")
            self.assertIn("API author", result[0])
            self.assertIn("https://www.pixiv.net/users/8", result[0])
            self.assertEqual(await self.route("再来几张"), ["more delivered through guarded pipeline"])
            self.assertEqual(await self.route("再来几张 10张"), ["more delivered through guarded pipeline"])
            resolve.reset_mock()
            self.assertIn("管理员", (await self.route("订阅画师"))[0])
            resolve.assert_not_awaited()
            self.assertEqual(calls, [(123, 3), (123, 10)])
            event = FakeEvent("/pixiv 订阅画师")
            event.is_admin = lambda: True
            self.assertEqual([result async for result in self.plugin.pixiv_search_illust(event)], ["subscribed"])
            self.assertEqual(calls[-1], "8")
        self.assertEqual(self.api_calls, [])

    async def test_delivery_commands_route_to_existing_guarded_handler(self):
        calls = []

        async def handle(event, action, argument=""):
            calls.append((action, argument))
            yield "existing admin handler"

        name = f"{PACKAGE}.handlers.delivery"
        with patch.dict(sys.modules, {name: module(name, handle_delivery_command=handle)}):
            for tail in ("待核实", "确认送达 123", "允许重发 456"):
                self.assertEqual(await self.route(tail), ["existing admin handler"])
        self.assertEqual(calls, [("delivery_status", ""), ("delivery_confirm", "123"), ("delivery_retry", "456")])

    async def test_bare_quote_sends_once_then_yields_only_none_with_final_stop(self):
        reply_type = sys.modules["astrbot.api.message_components"].Reply
        event = FakeEvent("查询作者")
        event.get_platform_name = lambda: "aiocqhttp"
        event.get_messages = lambda: [reply_type()]
        event.result = None
        event.set_result = lambda value: setattr(event, "result", value)
        event.stop_event = lambda: event.set_result(types.SimpleNamespace(stopped=True))
        event.send = AsyncMock(return_value=None)

        async def action(event, request):
            self.assertEqual(request.action, "quote_author")
            yield "author details"

        with patch.object(self.plugin, "_handle_pixiv_quote", action):
            values = [value async for value in self.plugin.pixiv_quote_phrase(event)]
        self.assertEqual(values, [None])
        event.send.assert_awaited_once_with("author details")
        self.assertTrue(event.result.stopped)
        # Core call_handler only replaces event results for non-None message results.
        self.assertTrue(all(value is None for value in values))

    async def test_bare_phrase_leaves_unquoted_private_and_unsupported_events_untouched(self):
        reply_type = sys.modules["astrbot.api.message_components"].Reply
        for platform, group, replies in (("other", "100", [reply_type()]),
                                          ("aiocqhttp", "", [reply_type()]),
                                          ("aiocqhttp", "100", [])):
            event = FakeEvent("查询作者")
            event.get_platform_name = lambda: platform
            event.get_group_id = lambda: group
            event.get_messages = lambda: replies
            event.stop_event = Mock()
            event.send = AsyncMock()
            self.assertEqual([value async for value in self.plugin.pixiv_quote_phrase(event)], [])
            event.send.assert_not_awaited()
            event.stop_event.assert_not_called()

    async def test_actual_core_regex_only_matches_complete_quote_phrases(self):
        source = ROOT.parent / "AstrBot/astrbot/core/star/filter/regex.py"
        if not source.is_file():
            self.skipTest("Adjacent AstrBot source is not available")
        name = "quote_regex_core_test"
        stubs = {
            "astrbot.core.config": module("astrbot.core.config", AstrBotConfig=object),
            "astrbot.core.platform.astr_message_event": module("astrbot.core.platform.astr_message_event", AstrMessageEvent=object),
            name: module(name, HandlerFilter=object, __path__=[]),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location(f"{name}.regex", source)
            core = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(core)
            regex_filter = core.RegexFilter(self.commands.QUOTE_BARE_PATTERN)
            for phrase in ("查询作者", "订阅画师", "再来几张", "再来几张 10张"):
                self.assertTrue(regex_filter.filter(FakeEvent(phrase), {}))
            for phrase in ("帮我查询作者", "查询作者 foo", "/pixiv 查询作者", "再来几张风景", "hello\n查询作者"):
                self.assertFalse(regex_filter.filter(FakeEvent(phrase), {}))

    async def test_help_lists_short_commands_without_pixiv_authentication(self):
        self.plugin.client_wrapper.authenticate = Mock(side_effect=AssertionError)
        result = await self.route("帮助")
        self.assertEqual(len(result), 1)
        for alias in ("搜图", "订阅", "取消订阅", "订阅列表", "暂停推送",
                      "恢复推送", "推送状态", "帮助"):
            self.assertIn(f"/pixiv {alias}", result[0])
        self.assertIn("随机", result[0])

    async def test_malformed_route_reports_error_without_invoking_operations(self):
        self.plugin.client_wrapper.authenticate = Mock(side_effect=AssertionError)
        for tail in ("搜图 初音 0张", "订阅 invalid", "暂停推送 extra"):
            with self.subTest(tail=tail):
                result = await self.route(tail)
                self.assertEqual(len(result), 1)
                self.assertIsInstance(result[0], str)
                self.assertIn("/pixiv", result[0])
        self.assertEqual(self.api_calls, [])

    async def test_concurrent_counts_do_not_mutate_shared_config_or_handler(self):
        config = self.plugin.pixiv_config
        handler = self.plugin.illust_handler
        snapshot = config.__dict__.copy()
        stored = config.config.copy()
        results = await asyncio.gather(
            self.route("搜图 one 1张"), self.route("搜图 ten 10张"),
            self.route("legacy tag"), self.route("搜图 default"),
        )
        self.assertEqual(results, [[("one", 1)], [("ten", 10)],
                                   [("legacy tag", 2)], [("default", 2)]])
        self.assertIs(self.plugin.illust_handler, handler)
        self.assertIs(handler.pixiv_config, config)
        self.assertEqual(config.__dict__, snapshot)
        self.assertEqual(config.config, stored)

    async def test_count_uses_shallow_config_copy_and_temporary_handler(self):
        configs = []
        original = self.main.IllustHandler

        def make_handler(client, config):
            self.assertIs(client, self.plugin.client_wrapper)
            self.assertIsNot(config, self.plugin.pixiv_config)
            self.assertIs(config.config, self.plugin.pixiv_config.config)
            self.assertIs(config.automatic_push_excluded_tags,
                          self.plugin.pixiv_config.automatic_push_excluded_tags)
            configs.append(config)
            return original(client, config)

        with patch.object(self.main, "IllustHandler", make_handler):
            self.assertEqual(await self.route("搜图 初音 4张"), [("初音", 4)])
            self.assertEqual(await self.route("搜图 初音"), [("初音", 2)])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].return_count, 4)

    async def test_count_does_not_mutate_global_config_on_search_failure(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("API unavailable")

        self.plugin.client_wrapper.call_pixiv_api = fail
        result = await self.route("搜图 初音 5张")
        self.assertIn("API unavailable", result[0])
        self.assertEqual(self.plugin.pixiv_config.return_count, 2)
        self.assertEqual(self.plugin.pixiv_config.config["return_count"], 2)

    def test_tail_fallback_and_command_boundary(self):
        tail = self.commands.extract_pixiv_tail
        self.assertEqual(tail("pixiv white  dress", "white"), "white  dress")
        self.assertEqual(tail(" /pixiv\t搜图\n初音 3张 ", "搜图"), "搜图\n初音 3张")
        self.assertEqual(tail("pixiv", "stale"), "")
        self.assertEqual(tail("pixiv_other foo", "fallback value"), "fallback value")


if __name__ == "__main__":
    unittest.main()
