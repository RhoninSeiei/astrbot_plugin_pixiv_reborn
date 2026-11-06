import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from utils.delivery_guard import DeliveryLedger


ROOT = Path(__file__).resolve().parents[1]


def load_handler():
    modules = {}
    for name, path in (("delivery_test", ROOT), ("delivery_test.handlers", ROOT / "handlers"),
                       ("delivery_test.utils", ROOT / "utils")):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        modules[name] = module
    spec = importlib.util.spec_from_file_location("delivery_test.handlers.delivery", ROOT / "handlers/delivery.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class DeliveryCommandsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = DeliveryLedger(Path(self.temp.name) / "ledger.db")
        self.handler = load_handler()
        self.handler.get_delivery_ledger = lambda: self.ledger
        self.session = "qq:GroupMessage:42"
        self.event = types.SimpleNamespace(
            unified_msg_origin=self.session, is_admin=lambda: False,
            get_group_id=lambda: "42", get_sender_id=lambda: "7",
            get_group=AsyncMock(return_value=types.SimpleNamespace(group_owner="7", group_admins=[])),
            plain_result=lambda value: value,
        )

    def tearDown(self):
        self.temp.cleanup()

    def unknown(self, session=None):
        session = session or self.session
        token, _ = self.ledger.begin(session, [10])
        self.ledger.finish(session, [10], token, "unknown")

    async def run_command(self, action, argument=""):
        return [message async for message in self.handler.handle_delivery_command(self.event, action, argument)]

    async def test_status_and_confirmation_are_exact_session_scoped(self):
        self.unknown("other:GroupMessage:42")
        self.assertIn("没有待核实", (await self.run_command("delivery_status"))[0])
        self.unknown()
        self.assertIn("10", (await self.run_command("delivery_status"))[0])
        await self.run_command("delivery_confirm", "10")
        self.assertEqual(self.ledger.status(self.session, [10])[10]["state"], "sent")
        self.assertEqual(self.ledger.status("other:GroupMessage:42", [10])[10]["state"], "unknown")

    async def test_retry_only_releases_unknown_and_does_not_send(self):
        self.unknown()
        result = await self.run_command("delivery_retry", "10")
        self.assertIn("本次未发送", result[0])
        self.assertEqual(self.ledger.status(self.session, [10])[10]["state"], "failed")

    async def test_regular_member_cannot_read_or_change_records(self):
        self.unknown()
        self.event.get_sender_id = lambda: "8"
        for action in ("delivery_status", "delivery_confirm", "delivery_retry"):
            self.assertIn("仅机器人管理员", (await self.run_command(action, "10"))[0])
        self.assertEqual(self.ledger.status(self.session, [10])[10]["state"], "unknown")

    async def test_pending_cannot_be_released_from_chat(self):
        self.ledger.begin(self.session, [10])
        for action in ("delivery_confirm", "delivery_retry"):
            self.assertIn("维护人员", (await self.run_command(action, "10"))[0])
        self.assertEqual(self.ledger.status(self.session, [10])[10]["state"], "pending")

    async def test_storage_failure_does_not_report_success(self):
        with patch.object(self.ledger, "unresolved", side_effect=OSError("disk")):
            self.assertIn("未解除防重限制", (await self.run_command("delivery_status"))[0])
