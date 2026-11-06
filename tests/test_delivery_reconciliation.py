import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

from tests.test_database_optimizations import import_database_for_temp_dir
from utils.delivery_guard import get_delivery_ledger


class DeliveryReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = import_database_for_temp_dir(self.temp.name)
        self.ledger = get_delivery_ledger()
        self.session = "bot:GroupMessage:42"

    def tearDown(self):
        if not self.database.db.is_closed():
            self.database.db.close()
        sys.modules.pop("utils.database", None)
        package = sys.modules.get("utils")
        if package is not None:
            package.__dict__.pop("database", None)
        self.temp.cleanup()

    def uncertain(self, session_id, illust_id):
        token, _ = self.ledger.begin(session_id, [illust_id])
        self.ledger.finish(session_id, [illust_id], token, "unknown")
        return token

    def test_admin_listing_and_resolution_are_exact_session_scoped(self):
        self.uncertain(self.session, 1)
        other = "other-bot:GroupMessage:42"
        self.uncertain(other, 1)
        rows = self.database.list_unresolved_deliveries(self.session)
        self.assertEqual([(row["illust_id"], row["state"]) for row in rows], [(1, "unknown")])
        self.assertTrue(self.database.resolve_delivery(self.session, 1, delivered=True))
        self.assertEqual(self.database.list_unresolved_deliveries(self.session), [])
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "sent")
        self.assertEqual(self.ledger.status(other, [1])[1]["state"], "unknown")
        self.assertFalse(self.database.resolve_delivery(self.session, 1, delivered=False))

    def test_admin_fresh_pending_is_refused_stale_pending_requires_explicit_resolution(self):
        self.ledger.begin(self.session, [1])
        self.assertFalse(self.database.resolve_delivery(self.session, 1, delivered=False))
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "pending")
        with closing(sqlite3.connect(self.ledger.path)) as connection, connection:
            connection.execute("UPDATE delivery_uncertainty SET updated_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.database.list_unresolved_deliveries(self.session)[0]["state"], "pending")
        self.assertTrue(self.database.resolve_delivery(self.session, 1, delivered=False))
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "failed")
        token, blocked = self.ledger.begin(self.session, [1])
        self.assertIsNotNone(token)
        self.assertFalse(blocked)

    def test_admin_resolution_requires_boolean_and_cannot_invent_delivery(self):
        self.uncertain(self.session, 1)
        self.assertFalse(self.database.resolve_delivery(self.session, 1, delivered="false"))
        self.assertFalse(self.database.resolve_delivery(self.session, 99, delivered=True))
        self.assertEqual(self.ledger.status(self.session, [1])[1]["state"], "unknown")

    def test_corrupt_ledger_is_not_reported_as_an_empty_queue(self):
        self.ledger.path.write_bytes(b"invalid SQLite database")
        with self.assertRaises(sqlite3.DatabaseError):
            self.database.list_unresolved_deliveries(self.session)
        with self.assertRaises(sqlite3.DatabaseError):
            self.database.resolve_delivery(self.session, 1, delivered=False)


if __name__ == "__main__":
    unittest.main()
