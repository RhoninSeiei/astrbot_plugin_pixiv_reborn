import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_subscribe_module():
    modules = {}
    for name, path in (
        ("subscribe_commands_test", ROOT),
        ("subscribe_commands_test.handlers", ROOT / "handlers"),
        ("subscribe_commands_test.utils", ROOT / "utils"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        modules[name] = module
    api = types.ModuleType("astrbot.api")
    api.logger = Mock()
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    database = types.ModuleType("subscribe_commands_test.utils.database")
    database.add_subscription = Mock(return_value=(True, "added"))
    database.remove_subscription = Mock(return_value=(True, "removed"))
    database.list_subscriptions = Mock(return_value=[])
    modules.update({
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": api,
        "astrbot.api.event": event,
        database.__name__: database,
    })
    name = "subscribe_commands_test.handlers.subscribe"
    spec = importlib.util.spec_from_file_location(name, ROOT / "handlers/subscribe.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class SubscribeCommandsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_subscribe_module()
        self.client = types.SimpleNamespace(user_detail=Mock(), user_illusts=Mock())
        self.wrapper = types.SimpleNamespace(
            client_api=self.client,
            authenticate=AsyncMock(return_value=True),
            call_pixiv_api=AsyncMock(side_effect=[
                types.SimpleNamespace(user=types.SimpleNamespace(name="artist")),
                types.SimpleNamespace(illusts=[types.SimpleNamespace(id=99)]),
            ]),
        )
        self.config = types.SimpleNamespace(
            subscription_enabled=True,
            get_auth_error_message=lambda: "authentication failed",
        )
        self.handler = self.module.SubscribeHandler(self.wrapper, self.config)
        self.event = types.SimpleNamespace(
            platform_meta=types.SimpleNamespace(id="qq"),
            get_message_type=lambda: types.SimpleNamespace(value="GroupMessage"),
            get_group_id=lambda: "group1",
            get_sender_id=lambda: "sender1",
            plain_result=lambda text: text,
        )

    async def collect(self, command, value):
        return [message async for message in command(self.event, value)]

    async def test_add_numeric_id_initializes_from_latest_work(self):
        messages = await self.collect(self.handler.pixiv_subscribe_add, "123")
        self.assertEqual(messages, ["added"])
        self.module.add_subscription.assert_called_once_with(
            "group1", "qq:GroupMessage:group1", "artist", "123", "artist", 99
        )
        self.assertEqual(self.wrapper.call_pixiv_api.await_args_list[0].args,
                         (self.client.user_detail, 123))
        self.assertEqual(self.wrapper.call_pixiv_api.await_args_list[1].args,
                         (self.client.user_illusts, 123))

    async def test_legacy_leading_zero_subscription_can_be_removed_and_not_duplicated(self):
        self.module.list_subscriptions.return_value = [
            types.SimpleNamespace(sub_type="artist", target_id="00123"),
        ]
        result = await self.collect(self.handler.pixiv_subscribe_add, "https://www.pixiv.net/users/123")
        self.assertIn("已经订阅", result[0])
        self.module.add_subscription.assert_not_called()
        self.wrapper.authenticate.assert_not_awaited()
        self.assertEqual(await self.collect(self.handler.pixiv_subscribe_remove, "123"), ["removed"])
        self.module.remove_subscription.assert_called_once_with("group1", "artist", "00123")

    async def test_add_and_remove_accept_official_artist_links(self):
        for value in (
            "https://www.pixiv.net/users/123",
            "https://pixiv.net/en/users/123/?utm_source=share#profile",
            "http://www.pixiv.net/en/users/123",
        ):
            with self.subTest(value=value):
                self.setUp()
                self.assertEqual(await self.collect(self.handler.pixiv_subscribe_add, value),
                                 ["added"])
                self.assertEqual(await self.collect(self.handler.pixiv_subscribe_remove, value),
                                 ["removed"])
                self.assertEqual(self.module.add_subscription.call_args.args[3], "123")
                self.module.remove_subscription.assert_called_once_with(
                    "group1", "artist", "123"
                )

    async def test_invalid_links_and_ids_do_not_call_api_or_database(self):
        for value in (
            "", "abc123", "123abc", "-1", "0", "１２３", "¹²³",
            "https://evil.example/users/123",
            "https://pixiv.net.evil.example/users/123",
            "https://evilpixiv.net/users/123",
            "https://pixiv.net@evil.example/users/123",
            "https://evil.example@pixiv.net/users/123",
            "https://www.pixiv.net:444/users/123",
            "https://www.pixiv.net./users/123",
            "https://www.pixiv.net/artworks/123",
            "https://www.pixiv.net/users/123/other",
            "https://www.pixiv.net/users/123abc",
            "https://www.pixiv.net/users/%31%32%33",
            "https://www.pixiv.net/\nusers/123",
            "https://www.pixiv.net\\@evil.example/users/123",
            "ftp://www.pixiv.net/users/123",
        ):
            with self.subTest(value=value):
                self.setUp()
                await self.collect(self.handler.pixiv_subscribe_add, value)
                await self.collect(self.handler.pixiv_subscribe_remove, value)
                self.wrapper.authenticate.assert_not_awaited()
                self.wrapper.call_pixiv_api.assert_not_awaited()
                self.module.add_subscription.assert_not_called()
                self.module.remove_subscription.assert_not_called()

    async def test_detail_or_latest_api_failure_prevents_subscription_write(self):
        detail = types.SimpleNamespace(user=types.SimpleNamespace(name="artist"))
        for responses in (
            [RuntimeError("detail unavailable")],
            [None],
            [types.SimpleNamespace(error={"message": "not found"})],
            [detail, RuntimeError("latest unavailable")],
            [detail, None],
            [detail, types.SimpleNamespace(error={"message": "unavailable"})],
            [detail, types.SimpleNamespace(illusts=None)],
            [detail, types.SimpleNamespace(illusts=[], error={"message": "failed"})],
        ):
            with self.subTest(responses=responses):
                self.setUp()
                self.wrapper.call_pixiv_api.side_effect = responses
                messages = await self.collect(self.handler.pixiv_subscribe_add, "123")
                self.module.add_subscription.assert_not_called()
                self.assertEqual(len(messages), 1)
                self.assertIn("未添加订阅", messages[0])
                self.assertIn("重试", messages[0])

    async def test_successful_empty_artist_uses_zero_baseline(self):
        self.wrapper.call_pixiv_api.side_effect = [
            types.SimpleNamespace(user=types.SimpleNamespace(name="artist")),
            types.SimpleNamespace(illusts=[]),
        ]
        self.assertEqual(await self.collect(self.handler.pixiv_subscribe_add, "123"),
                         ["added"])
        self.assertEqual(self.module.add_subscription.call_args.args[-1], 0)

    async def test_authentication_failure_prevents_subscription_write(self):
        self.wrapper.authenticate.return_value = False
        await self.collect(self.handler.pixiv_subscribe_add, "123")
        self.module.add_subscription.assert_not_called()
        self.wrapper.call_pixiv_api.assert_not_awaited()

    async def test_remove_numeric_id_does_not_require_api(self):
        self.assertEqual(await self.collect(self.handler.pixiv_subscribe_remove, "123"),
                         ["removed"])
        self.module.remove_subscription.assert_called_once_with("group1", "artist", "123")
        self.wrapper.authenticate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
