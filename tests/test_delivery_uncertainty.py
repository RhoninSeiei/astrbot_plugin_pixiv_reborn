import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from utils.delivery_guard import (
    DeliveryLedger,
    delivery_status,
    guarded_send,
    resolve_delivery,
)
from tests.test_random_push_retry import FakeActionFailedTimeout


class DeliveryUncertaintyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "delivery.db"
        self.ledger = DeliveryLedger(self.path)
        self.session = "bot:GroupMessage:42"
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    async def send(self):
        self.calls.append("transport")
        states = await delivery_status(self.session, [1, 2], ledger=self.ledger)
        self.assertEqual({row["state"] for row in states.values()}, {"pending"})
        return {"message_id": "receipt-1"}

    async def test_pending_commits_before_transport_and_receipt_is_retained(self):
        outcome = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        self.assertEqual(outcome.status, "sent")
        self.assertEqual(outcome.receipt, {"message_id": "receipt-1"})
        rows = await delivery_status(self.session, [1, 2], ledger=self.ledger)
        self.assertEqual({row["state"] for row in rows.values()}, {"sent"})
        self.assertEqual(rows[1]["receipt_id"], "receipt-1")

    async def test_unknown_survives_new_ledger_and_blocks_entire_overlapping_batch(self):
        async def timeout():
            self.calls.append("timeout")
            raise TimeoutError("websocket response timed out")

        result = await guarded_send(self.session, [1], timeout, ledger=self.ledger)
        self.assertEqual(result.status, "unknown")
        reloaded = DeliveryLedger(self.path)
        result = await guarded_send(self.session, [1, 2], self.send, ledger=reloaded)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(self.calls, ["timeout"])
        rows = await delivery_status(self.session, [1, 2], ledger=reloaded)
        self.assertEqual(rows[1]["state"], "unknown")
        self.assertNotIn(2, rows)
        # Expired random-push history must not release uncertainty.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE delivery_uncertainty SET updated_at = '2000-01-01'")
        result = await guarded_send(self.session, [1], timeout, ledger=reloaded)
        self.assertEqual(result.status, "blocked")

    async def test_known_accepted_timeout_is_sent_not_unknown(self):
        async def accepted():
            raise FakeActionFailedTimeout()

        result = await guarded_send(self.session, [1], accepted, ledger=self.ledger)
        self.assertEqual(result.status, "sent")
        rows = await delivery_status(self.session, [1], ledger=self.ledger)
        self.assertEqual(rows[1]["state"], "sent")

    async def test_definite_media_failure_releases_attempt_and_preserves_retry(self):
        async def failed():
            self.calls.append("failed")
            raise RuntimeError("rich media transfer failed")

        result = await guarded_send(self.session, [1, 2], failed, ledger=self.ledger)
        self.assertEqual(result.status, "failed")
        result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        self.assertEqual(result.status, "sent")
        self.assertEqual(self.calls, ["failed", "transport"])

    async def test_cancellation_leaves_pending_and_reload_cannot_resend(self):
        entered = asyncio.Event()

        async def wait_forever():
            self.calls.append("entered")
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(guarded_send(
            self.session, [1], wait_forever, ledger=self.ledger
        ))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        reloaded = DeliveryLedger(self.path)
        result = await guarded_send(self.session, [1], self.send, ledger=reloaded)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(self.calls, ["entered"])
        rows = await delivery_status(self.session, [1], ledger=reloaded)
        self.assertEqual(rows[1]["state"], "pending")

    async def test_concurrent_instances_only_one_enters_transport(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held():
            self.calls.append("entered")
            entered.set()
            await release.wait()

        task = asyncio.create_task(guarded_send(self.session, [1], held, ledger=self.ledger))
        await asyncio.wait_for(entered.wait(), 2)
        other = await guarded_send(self.session, [1], self.send, ledger=DeliveryLedger(self.path))
        release.set()
        first = await task
        self.assertEqual((first.status, other.status), ("sent", "blocked"))
        self.assertEqual(self.calls, ["entered"])

    async def test_ledger_unwritable_blocks_transport(self):
        # A real filesystem error rather than a mocked persistence result.
        ledger = DeliveryLedger(self.path / "child.db")
        self.path.write_bytes(b"not a directory")
        result = await guarded_send(self.session, [1], self.send, ledger=ledger)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(self.calls, [])

    async def test_post_send_write_failure_keeps_pending_and_blocks_retry(self):
        async def sabotage():
            self.calls.append("transport")
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("""CREATE TRIGGER deny_finish BEFORE UPDATE ON delivery_uncertainty
                              BEGIN SELECT RAISE(FAIL, 'disk failure'); END""")

        result = await guarded_send(self.session, [1], sabotage, ledger=self.ledger)
        self.assertEqual(result.status, "unknown")
        result = await guarded_send(self.session, [1], sabotage, ledger=self.ledger)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(self.calls, ["transport"])

    async def test_manual_resolution_requires_current_token_and_evidence(self):
        async def timeout():
            raise TimeoutError()

        await guarded_send(self.session, [1], timeout, ledger=self.ledger)
        rows = await delivery_status(self.session, [1], ledger=self.ledger)
        token = rows[1]["attempt_token"]
        with self.assertRaises(ValueError):
            await resolve_delivery(self.session, [1], attempt_token="stale", resolution="failed",
                                   note="operator checked adapter history", ledger=self.ledger)
        with self.assertRaises(ValueError):
            await resolve_delivery(self.session, [1], attempt_token=token, resolution="failed",
                                   note="", ledger=self.ledger)
        await resolve_delivery(self.session, [1], attempt_token=token, resolution="failed",
                               note="operator verified not accepted", ledger=self.ledger)
        result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        self.assertEqual(result.status, "sent")

    async def test_same_illust_in_other_unified_session_is_independent(self):
        async def timeout():
            raise TimeoutError()

        await guarded_send(self.session, [1], timeout, ledger=self.ledger)
        other = "other:GroupMessage:42"
        result = await guarded_send(other, [1], lambda: asyncio.sleep(0), ledger=self.ledger)
        self.assertEqual(result.status, "sent")

    async def test_terminal_success_does_not_replace_custom_repeat_policy(self):
        for _ in range(2):
            result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
            self.assertEqual(result.status, "sent")
        self.assertEqual(self.calls, ["transport", "transport"])

    async def test_exact_false_is_failure_but_none_is_success(self):
        result = await guarded_send(self.session, [1], lambda: asyncio.sleep(0, result=False),
                                    ledger=self.ledger)
        self.assertEqual(result.status, "failed")
        result = await guarded_send(self.session, [1], lambda: asyncio.sleep(0), ledger=self.ledger)
        self.assertEqual(result.status, "sent")

    async def test_retcode_1200_media_failure_is_retryable_without_timeout_evidence(self):
        class MediaRejected(Exception):
            retcode = 1200
            wording = "rich media transfer failed"

        async def rejected():
            raise MediaRejected("rich media transfer failed")

        result = await guarded_send(self.session, [1, 2], rejected, ledger=self.ledger)
        self.assertEqual(result.status, "failed")
        result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        self.assertEqual(result.status, "sent")

    async def test_retcode_1200_timeout_near_miss_and_mixed_media_timeout_are_unknown(self):
        class RejectedTimeout(Exception):
            retcode = 1200

        for number, message in enumerate((
            'Timeout: sendMsg EventRet: {"result":1,"errMsg":""}',
            'rich media transfer failed: websocket timed out',
        ), start=1):
            async def timeout():
                raise RejectedTimeout(message)

            result = await guarded_send(self.session, [number], timeout, ledger=self.ledger)
            self.assertEqual(result.status, "unknown")
            result = await guarded_send(self.session, [number], self.send, ledger=self.ledger)
            self.assertEqual(result.status, "blocked")

    async def test_fresh_pending_cannot_be_manually_released_and_stale_never_auto_retries(self):
        token, _ = self.ledger.begin(self.session, [1])
        with self.assertRaises(ValueError):
            await resolve_delivery(self.session, [1], attempt_token=token, resolution="failed",
                                   note="operator checked transport", ledger=self.ledger)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE delivery_uncertainty SET updated_at='2000-01-01T00:00:00+00:00'")
        result = await guarded_send(self.session, [1], self.send, ledger=self.ledger)
        self.assertEqual(result.status, "blocked")
        await resolve_delivery(self.session, [1], attempt_token=token, resolution="failed",
                               note="maintenance interval; adapter verified no send", ledger=self.ledger)
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "failed")

    async def test_old_finalization_cannot_override_manual_resolution_or_new_attempt(self):
        token, _ = self.ledger.begin(self.session, [1])
        self.ledger.finish(self.session, [1], token, "unknown")
        self.ledger.reconcile(self.session, [1], token, "failed", "verified not sent")
        new_token, _ = self.ledger.begin(self.session, [1])
        with self.assertRaises(ValueError):
            self.ledger.finish(self.session, [1], token, "sent")
        row = self.ledger.status(self.session, [1])[1]
        self.assertEqual((row["state"], row["attempt_token"]), ("pending", new_token))

    async def test_batch_finalization_is_atomic_on_token_mismatch(self):
        token, _ = self.ledger.begin(self.session, [1, 2])
        self.ledger.finish(self.session, [2], token, "unknown")
        with self.assertRaises(ValueError):
            self.ledger.finish(self.session, [1, 2], token, "sent")
        rows = self.ledger.status(self.session, [1, 2])
        self.assertEqual((rows[1]["state"], rows[2]["state"]), ("pending", "unknown"))

    async def test_scheduled_atomic_claim_rechecks_recent_sent_but_deliberate_requery_is_allowed(self):
        await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger,
                                    sent_retention_days=7)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(self.calls), 1)
        result = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger)
        self.assertEqual(result.status, "sent")
        self.assertEqual(len(self.calls), 2)

    async def test_response_disconnect_payload_error_and_unmatched_exception_are_unknown(self):
        # These names/messages cover adapters without importing an optional client.
        ServerDisconnectedError = type("ServerDisconnectedError", (Exception,), {})
        ClientPayloadError = type("ClientPayloadError", (Exception,), {})
        errors = (
            ServerDisconnectedError("Server disconnected"),
            ClientPayloadError("Response payload is not completed"),
            RuntimeError("unrecognized post-dispatch error"),
            RuntimeError("send failed"),
            RuntimeError("rich media transfer failed: Server disconnected"),
        )
        calls = []
        for illust_id, error in enumerate(errors, start=1):
            async def broken_response():
                calls.append(illust_id)
                raise error

            result = await guarded_send(self.session, [illust_id], broken_response, ledger=self.ledger)
            self.assertEqual(result.status, "unknown")
            result = await guarded_send(self.session, [illust_id], broken_response, ledger=DeliveryLedger(self.path))
            self.assertEqual(result.status, "blocked")
        self.assertEqual(calls, list(range(1, len(errors) + 1)))

    async def test_reject_sent_rechecks_after_both_entries_observed_unsent(self):
        other = DeliveryLedger(self.path)
        self.assertEqual(await delivery_status(self.session, [1, 2], ledger=self.ledger), {})
        self.assertEqual(await delivery_status(self.session, [1, 2], ledger=other), {})
        first = await guarded_send(self.session, [1, 2], self.send, ledger=self.ledger,
                                   reject_sent=True)
        self.assertEqual(first.status, "sent")
        second = await guarded_send(self.session, [1, 2], self.send, ledger=other,
                                    reject_sent=True)
        self.assertEqual(second.status, "blocked")
        self.assertEqual(second.blocked_ids, frozenset({1, 2}))
        self.assertEqual(self.calls, ["transport"])
        self.assertEqual({row["state"] for row in other.status(self.session, [1, 2]).values()}, {"sent"})


if __name__ == "__main__":
    unittest.main()
