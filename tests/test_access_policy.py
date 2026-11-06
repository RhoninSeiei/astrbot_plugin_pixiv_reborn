import types
import unittest

from utils.access_policy import can_manage_chat, require_management, resolve_content_mode


class Event:
    def __init__(self, admin=False, group=True, owner="2", admins=()):
        self.admin, self.group, self.owner, self.admins = admin, group, owner, admins

    def is_admin(self):
        return self.admin

    def get_group_id(self):
        return "100" if self.group else ""

    def get_sender_id(self):
        return "1"

    async def get_group(self):
        return types.SimpleNamespace(group_owner=self.owner, group_admins=self.admins)

    def plain_result(self, text):
        return text


class AccessPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_roles_and_private_scope(self):
        self.assertTrue(await can_manage_chat(Event(admin=True)))
        self.assertTrue(await can_manage_chat(Event(owner="1")))
        self.assertTrue(await can_manage_chat(Event(admins=[1])))
        self.assertTrue(await can_manage_chat(Event(group=False)))
        self.assertFalse(await can_manage_chat(Event()))

    async def test_lookup_failure_does_not_grant_access(self):
        event = Event()
        async def fail():
            raise RuntimeError("unavailable")
        event.get_group = fail
        self.assertFalse(await can_manage_chat(event))

    async def test_guard_blocks_mutation_but_allows_admin(self):
        changes = []
        @require_management
        async def mutate(self, event):
            changes.append(1)
            yield "ok"
        denied = [r async for r in mutate(None, Event())]
        self.assertEqual(changes, [])
        self.assertEqual(len(denied), 1)
        self.assertEqual([r async for r in mutate(None, Event(admin=True))], ["ok"])
        self.assertEqual(changes, [1])

    def test_request_cannot_relax_content_policy(self):
        with self.assertRaises(ValueError):
            resolve_content_mode("过滤 R18", "r18")
        with self.assertRaises(ValueError):
            resolve_content_mode("仅 R18", "safe")
        self.assertEqual(resolve_content_mode("允许 R18", "r18"), "仅 R18")
        self.assertEqual(resolve_content_mode("允许 R18", "safe"), "过滤 R18")

    async def test_global_config_requires_bot_admin(self):
        from tests.test_config_defaults import install_import_stubs, RecordingConfig
        install_import_stubs()
        from utils.config import PixivConfig, PixivConfigManager
        saved = RecordingConfig()
        manager = PixivConfigManager(PixivConfig(saved))
        await manager.handle_config_command(Event(owner="1"), "return_count", "2")
        self.assertEqual(saved.save_calls, 0)
        await manager.handle_config_command(Event(admin=True), "return_count", "2")
        self.assertEqual(saved.save_calls, 1)
        self.assertEqual(saved["return_count"], 2)
        self.assertEqual(PixivConfig(saved).return_count, 2)
        self.assertFalse(manager.validate_and_set_config("fanbox_cookie", "secret")[0])
        self.assertTrue(manager.validate_and_set_config("refresh_token_interval_minutes", "120")[0])
        self.assertEqual(PixivConfig(saved).refresh_interval, 120)


if __name__ == "__main__":
    unittest.main()
