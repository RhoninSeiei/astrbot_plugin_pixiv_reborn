import asyncio
import sys
import types
import unittest


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def install_import_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()

    pixivpy3 = types.ModuleType("pixivpy3")

    class FakePixivError(Exception):
        pass

    class FakeApi:
        def __init__(self, *args, **kwargs):
            pass

    pixivpy3.PixivError = FakePixivError
    pixivpy3.AppPixivAPI = FakeApi
    pixivpy3.ByPassSniApi = FakeApi

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["pixivpy3"] = pixivpy3


def import_client_module():
    sys.modules.pop("core.client", None)
    install_import_stubs()
    from core import client

    return client


class FakeAuthClient:
    def __init__(self):
        self.calls = 0

    def auth(self, refresh_token):
        self.calls += 1


class FakeConfig:
    refresh_token = "refresh-token"
    refresh_interval = 1


class PixivClientAuthTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        sys.modules.pop("core.client", None)

    def make_wrapper(self, client_module, client_api=None):
        wrapper = object.__new__(client_module.PixivClientWrapper)
        wrapper.pixiv_config = FakeConfig()
        wrapper.client_api = client_api or FakeAuthClient()
        wrapper._refresh_task = None
        wrapper._auth_lock = asyncio.Lock()
        wrapper._last_auth_success_at = None
        wrapper._last_auth_failure_at = None
        wrapper._auth_ttl_seconds = 30 * 60
        wrapper._auth_failure_cooldown_seconds = 30
        return wrapper

    async def test_concurrent_authenticate_uses_single_auth_call(self):
        client_module = import_client_module()
        fake_client = FakeAuthClient()
        wrapper = self.make_wrapper(client_module, fake_client)

        results = await asyncio.gather(
            *(wrapper.authenticate() for _ in range(5))
        )

        self.assertEqual(results, [True, True, True, True, True])
        self.assertEqual(fake_client.calls, 1)

    async def test_cached_authenticate_skips_repeated_auth_call(self):
        client_module = import_client_module()
        fake_client = FakeAuthClient()
        wrapper = self.make_wrapper(client_module, fake_client)

        self.assertTrue(await wrapper.authenticate())
        self.assertTrue(await wrapper.authenticate())

        self.assertEqual(fake_client.calls, 1)

    async def test_periodic_token_refresh_uses_threaded_auth(self):
        client_module = import_client_module()
        wrapper = self.make_wrapper(client_module)
        calls = []
        sleep_calls = 0
        original_sleep = client_module.asyncio.sleep
        original_to_thread = client_module.asyncio.to_thread

        async def fake_sleep(delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls > 1:
                raise asyncio.CancelledError

        async def fake_to_thread(func, *args, **kwargs):
            calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        client_module.asyncio.sleep = fake_sleep
        client_module.asyncio.to_thread = fake_to_thread
        try:
            await wrapper.periodic_token_refresh()
        finally:
            client_module.asyncio.sleep = original_sleep
            client_module.asyncio.to_thread = original_to_thread

        self.assertEqual(len(calls), 1)
        self.assertEqual(wrapper.client_api.calls, 1)


if __name__ == "__main__":
    unittest.main()
