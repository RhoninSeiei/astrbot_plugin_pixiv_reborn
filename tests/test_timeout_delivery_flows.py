import asyncio
import functools
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.test_random_push_retry import (
    FakeActionFailedTimeout,
    FakeIllust,
    FakeMessageChain,
    install_import_stubs,
    remove_import_stubs,
)
from tests.test_subscription_delivery import load_subscription_module, FakeImage
from tests import test_random_push_retry as random_helpers
from utils import delivery_guard


class TimeoutDeliveryFlowsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = delivery_guard.DeliveryLedger(Path(self.temp.name) / "delivery.db")
        self.session = "default:GroupMessage:42"
        self.messages = []
        self.generated = []
        self.error = TimeoutError("websocket response timed out")

    def tearDown(self):
        remove_import_stubs()
        self.temp.cleanup()

    async def transport(self, session_id, message):
        self.messages.append((session_id, message))
        if self.error is not None:
            raise self.error

    async def image(self, client, event, illust, detail, show_details=True, **kwargs):
        self.generated.append((illust.id, kwargs))
        chain = FakeMessageChain()
        chain.chain = [FakeImage(), FakeImage()]
        yield chain

    def random_service(self):
        install_import_stubs(self.temp.name)
        from utils import random_search

        helper = random_helpers.RandomPushRetryTest()
        service = helper._make_service(random_search)
        service.context = types.SimpleNamespace(send_message=self.transport)
        return random_search, service, helper._make_config(random_search, return_count=2)

    async def test_random_unknown_stops_retry_fallback_and_new_service_schedule(self):
        module, service, config = self.random_service()
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image), \
             patch.object(module.random, "shuffle", lambda items: None):
            result = await service._send_random_illusts_with_fallback(
                "42", self.session, "tag", "test", [FakeIllust(1), FakeIllust(2)], config
            )
            self.assertFalse(result.completed)
            self.assertEqual((result.sent_count, result.sent_image_count), (0, 0))
            self.assertEqual(len(self.messages), 1)
            # New instance, same persistent database: cannot resend or substitute.
            reloaded = object.__new__(module.RandomSearchService)
            reloaded.client, reloaded.context = service.client, service.context
            self.error = None
            result = await reloaded._send_random_illusts_with_fallback(
                "42", self.session, "ranking", "other", [FakeIllust(1), FakeIllust(2)], config
            )
            self.assertFalse(result.completed)
            self.assertEqual(len(self.messages), 1)
            self.assertEqual([entry[0] for entry in self.generated], [1, 1])

    async def test_random_definite_failure_keeps_three_retries_then_candidate_fallback(self):
        module, service, config = self.random_service()
        self.error = RuntimeError("rich media transfer failed")
        sends = 0

        async def flaky(session_id, message):
            nonlocal sends
            sends += 1
            if sends <= 3:
                raise self.error

        service.context.send_message = flaky
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image), \
             patch.object(module.asyncio, "sleep", new=AsyncMock()), \
             patch.object(module.random, "shuffle", lambda items: None):
            result = await service._send_random_illusts_with_fallback(
                "42", self.session, "tag", "test", [FakeIllust(1), FakeIllust(2)], config
            )
        self.assertTrue(result.completed)
        self.assertEqual(sends, 4)
        self.assertEqual(result.sent_count, 1)
        rows = self.ledger.status(self.session, [1, 2])
        self.assertEqual((rows[1]["state"], rows[2]["state"]), ("failed", "sent"))

    async def test_random_manual_confirm_is_a_durable_cache_without_sentillust_row(self):
        module, service, config = self.random_service()
        token, _ = self.ledger.begin(self.session, [1])
        self.ledger.finish(self.session, [1], token, "unknown")
        self.ledger.reconcile(self.session, [1], token, "sent", "admin verified adapter history")
        self.error = None
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image), \
             patch.object(module.random, "shuffle", lambda items: None):
            result = await service._send_random_illusts_with_fallback(
                "42", self.session, "tag", "test", [FakeIllust(1), FakeIllust(2)], config
            )
        self.assertEqual([entry[0] for entry in self.generated], [2])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual((result.sent_count, result.sent_image_count), (1, 1))

    async def test_random_success_before_sentillust_crash_is_not_resent_next_schedule(self):
        module, service, config = self.random_service()
        self.error = None
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image), \
             patch.object(module, "add_sent_illust", side_effect=RuntimeError("process stopped before history commit")):
            with self.assertRaises(RuntimeError):
                await service._send_random_illusts_with_fallback(
                    "42", self.session, "tag", "test", [FakeIllust(1)], config
                )
            result = await service._send_random_illusts_with_fallback(
                "42", self.session, "ranking", "other", [FakeIllust(1)], config
            )
        self.assertEqual(result.sent_count, 0)
        self.assertFalse(result.had_sendable_candidates)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual([entry[0] for entry in self.generated], [1])

    async def test_random_durable_cache_honors_configured_retention_expiry(self):
        module, service, config = self.random_service()
        service.pixiv_config = types.SimpleNamespace(random_sent_illust_retention_days=3)
        token, _ = self.ledger.begin(self.session, [1])
        self.ledger.finish(self.session, [1], token, "sent")
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("UPDATE delivery_uncertainty SET updated_at=?",
                       ((datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),))
        self.error = None
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image):
            result = await service._send_random_illusts_with_fallback(
                "42", self.session, "tag", "test", [FakeIllust(1)], config
            )
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(len(self.messages), 1)

    def subscription_service(self):
        module = load_subscription_module()
        service = object.__new__(module.SubscriptionService)
        service.client = object()
        service.context = types.SimpleNamespace(send_message=self.transport)
        service.pixiv_config = types.SimpleNamespace(
            automatic_push_excluded_tags=[], show_details=True, subscription_force_forward=False
        )
        return module, service

    def sub(self):
        return types.SimpleNamespace(
            session_id=self.session, chat_id="42", sub_type="artist",
            target_id="7", target_name="artist", last_notified_illust_id=0,
        )

    def bind_guard(self, module):
        return patch.multiple(
            module,
            guarded_send=functools.partial(delivery_guard.guarded_send, ledger=self.ledger),
            delivery_status=functools.partial(delivery_guard.delivery_status, ledger=self.ledger),
            unresolved_deliveries=functools.partial(delivery_guard.unresolved_deliveries, ledger=self.ledger),
            send_pixiv_image=self.image,
        )

    async def test_subscription_unknown_freezes_cursor_even_if_later_filter_changes(self):
        module, service = self.subscription_service()
        cursors = []
        with self.bind_guard(module), \
             patch.object(module, "update_last_notified_id", side_effect=lambda *args: cursors.append(args[-1])), \
             patch.object(module.asyncio, "sleep", new=AsyncMock()):
            await service.check_artist_updates(self.sub(), [FakeIllust(3), FakeIllust(2), FakeIllust(1)])
            self.assertEqual(cursors, [])
            self.assertEqual([row[0] for row in self.generated], [1])
            # Changing exclusions cannot skip unresolved ID 1 and commit ID 2/3.
            self.error = None
            with patch.object(module, "filter_items", return_value=([], [])):
                await service.check_artist_updates(self.sub(), [FakeIllust(3), FakeIllust(2), FakeIllust(1)])
            self.assertEqual(cursors, [])
            self.assertEqual(len(self.messages), 1)

    async def test_subscription_unknown_outside_latest_window_still_blocks_cursor(self):
        module, service = self.subscription_service()
        cursors = []
        with self.bind_guard(module), \
             patch.object(module, "update_last_notified_id", side_effect=lambda *args: cursors.append(args[-1])):
            await service.check_artist_updates(self.sub(), [FakeIllust(1)])
            self.error = None
            # ID 1 has disappeared from the API's newest-30 page.
            await service.check_artist_updates(self.sub(), [FakeIllust(32), FakeIllust(31)])
            with patch.object(module, "filter_items", return_value=([], [])):
                await service.check_artist_updates(self.sub(), [FakeIllust(33)])
        self.assertEqual(cursors, [])
        self.assertEqual([entry[0] for entry in self.generated], [1])
        self.assertEqual(len(self.messages), 1)

    async def test_random_outer_source_fallback_stops_after_unknown(self):
        module, service, config = self.random_service()
        service.pixiv_config = types.SimpleNamespace(
            random_search_empty_retry_enabled=True, random_search_empty_retry_sources=1
        )
        sources = []

        async def tag(chat_id, entry):
            sources.append("tag")
            return await service._send_random_illusts_with_fallback(
                chat_id, self.session, "tag", "test", [FakeIllust(1), FakeIllust(2)], config
            )

        async def ranking(chat_id, entry):
            sources.append("ranking")
            raise AssertionError("unknown must not trigger source fallback")

        service._execute_tag_search, service._execute_ranking_search = tag, ranking
        with patch.object(delivery_guard, "get_delivery_ledger", return_value=self.ledger), \
             patch.object(module, "send_pixiv_image", self.image), \
             patch.object(module, "get_random_tags", return_value=[types.SimpleNamespace(tag="tag")]), \
             patch.object(module, "get_random_rankings", return_value=[types.SimpleNamespace(mode="day")]), \
             patch.object(module.random, "choice", lambda items: items[0]), \
             patch.object(module.random, "shuffle", lambda items: None):
            count = await service.execute_search_for_group("42")
        self.assertEqual(count, 0)
        self.assertEqual(sources, ["tag"])
        self.assertEqual(len(self.messages), 1)

    async def test_subscription_accepted_timeout_advances_and_requests_all_pages(self):
        module, service = self.subscription_service()
        self.error = FakeActionFailedTimeout()
        cursors = []
        with self.bind_guard(module), \
             patch.object(module, "update_last_notified_id", side_effect=lambda *args: cursors.append(args[-1])), \
             patch.object(module.asyncio, "sleep", new=AsyncMock()):
            await service.check_artist_updates(self.sub(), [FakeIllust(1)])
        self.assertEqual(cursors, [1])
        self.assertEqual(len(self.messages), 1)
        self.assertTrue(self.generated[0][1]["send_all_pages"])

    async def test_subscription_sent_survives_crash_before_cursor_and_recovers_without_resend(self):
        module, service = self.subscription_service()
        self.error = None
        with self.bind_guard(module), \
             patch.object(module, "update_last_notified_id", side_effect=RuntimeError("cursor commit failed")):
            with self.assertRaises(RuntimeError):
                await service.check_artist_updates(self.sub(), [FakeIllust(1)])
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "sent")
        reloaded_module, reloaded = self.subscription_service()
        cursors = []
        with self.bind_guard(reloaded_module), \
             patch.object(reloaded_module, "update_last_notified_id", side_effect=lambda *args: cursors.append(args[-1])):
            await reloaded.check_artist_updates(self.sub(), [FakeIllust(1)])
        self.assertEqual(cursors, [1])
        self.assertEqual(len(self.messages), 1)

    async def test_subscription_post_send_finalize_failure_freezes_and_blocks_reload(self):
        module, service = self.subscription_service()
        self.error = None

        async def sabotage(session_id, message):
            self.messages.append((session_id, message))
            with closing(sqlite3.connect(self.ledger.path)) as db, db:
                db.execute("""CREATE TRIGGER deny_finish BEFORE UPDATE ON delivery_uncertainty
                              BEGIN SELECT RAISE(FAIL, 'disk failure'); END""")

        service.context.send_message = sabotage
        cursors = []
        with self.bind_guard(module), \
             patch.object(module, "update_last_notified_id", side_effect=lambda *args: cursors.append(args[-1])):
            await service.check_artist_updates(self.sub(), [FakeIllust(2), FakeIllust(1)])
            await service.check_artist_updates(self.sub(), [FakeIllust(2), FakeIllust(1)])
        self.assertEqual(cursors, [])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "pending")

    async def test_subscription_concurrent_prechecks_unsent_do_not_duplicate_after_first_finishes(self):
        module, service = self.subscription_service()
        self.error = None
        both_prepared = asyncio.Event()
        first_completed = asyncio.Event()
        prepared = 0

        async def prepare(client, event, illust, detail, show_details=True, **kwargs):
            nonlocal prepared
            prepared += 1
            if prepared == 1:
                await both_prepared.wait()
            else:
                both_prepared.set()
                await first_completed.wait()
            chain = FakeMessageChain()
            chain.chain = [FakeImage(), FakeImage()]
            yield chain

        async def first_entry():
            try:
                return await service.send_update(self.sub(), FakeIllust(1))
            finally:
                first_completed.set()

        with self.bind_guard(module), patch.object(module, "send_pixiv_image", prepare):
            first, second = await asyncio.wait_for(asyncio.gather(
                first_entry(), service.send_update(self.sub(), FakeIllust(1)),
            ), timeout=5)
            self.assertTrue(first)
            self.assertFalse(second)
            # Next cycle recovers the confirmed result without another transport.
            self.assertTrue(await service.send_update(self.sub(), FakeIllust(1)))
        self.assertEqual(prepared, 2)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "sent")


if __name__ == "__main__":
    unittest.main()
