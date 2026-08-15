import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


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
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.star"] = astrbot_star


def import_database_for_temp_dir(temp_dir):
    sys.modules.pop("utils.database", None)
    utils_module = sys.modules.get("utils")
    if utils_module is not None and hasattr(utils_module, "database"):
        delattr(utils_module, "database")
    install_import_stubs(temp_dir)
    from utils import database

    return database


class DatabaseOptimizationTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("utils.database", None)
        utils_module = sys.modules.get("utils")
        if utils_module is not None and hasattr(utils_module, "database"):
            delattr(utils_module, "database")

    def test_sent_illust_indexes_are_created_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)
            try:
                database.initialize_database()
                database.initialize_database()

                indexes = {
                    index.name: tuple(index.columns)
                    for index in database.db.get_indexes("sentillust")
                }

                self.assertIn("idx_sentillust_chat_illust", indexes)
                self.assertEqual(
                    indexes["idx_sentillust_chat_illust"], ("chat_id", "illust_id")
                )
                self.assertIn("idx_sentillust_chat_sent_at", indexes)
                self.assertEqual(
                    indexes["idx_sentillust_chat_sent_at"], ("chat_id", "sent_at")
                )
            finally:
                if not database.db.is_closed():
                    database.db.close()

    def test_partition_sent_illusts_is_group_scoped_and_expires_old_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)
            try:
                database.initialize_database()
                now = datetime(2026, 8, 16, 12, 0, 0)
                database.SentIllust.create(
                    illust_id=1,
                    chat_id="group-a",
                    sent_at=now - timedelta(days=5),
                )
                database.SentIllust.create(
                    illust_id=2,
                    chat_id="group-a",
                    sent_at=now - timedelta(days=40),
                )
                database.SentIllust.create(
                    illust_id=3,
                    chat_id="group-a",
                    sent_at=now - timedelta(days=46),
                )
                database.SentIllust.create(
                    illust_id=5,
                    chat_id="group-a",
                    sent_at=now - timedelta(days=45),
                )
                items = [types.SimpleNamespace(id=item_id) for item_id in range(1, 6)]

                unsent, recent = database.partition_sent_illusts(
                    items,
                    "group-a",
                    retention_days=45,
                    now=now,
                )
                other_group_unsent, other_group_recent = (
                    database.partition_sent_illusts(
                        items,
                        "group-b",
                        retention_days=45,
                        now=now,
                    )
                )

                self.assertEqual([item.id for item in unsent], [3, 4])
                self.assertEqual([item.id for item in recent], [5, 2, 1])
                self.assertEqual(
                    [item.id for item in other_group_unsent], [1, 2, 3, 4, 5]
                )
                self.assertEqual(other_group_recent, [])
            finally:
                if not database.db.is_closed():
                    database.db.close()

    def test_add_sent_illust_refreshes_existing_record_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)
            try:
                database.initialize_database()
                old_time = datetime(2026, 6, 1, 12, 0, 0)
                new_time = datetime(2026, 8, 16, 12, 0, 0)

                database.add_sent_illust(71, "group-a", sent_at=old_time)
                database.add_sent_illust(71, "group-a", sent_at=new_time)

                record = database.SentIllust.get_by_id((71, "group-a"))
                self.assertEqual(record.sent_at, new_time)
            finally:
                if not database.db.is_closed():
                    database.db.close()

    def test_random_search_groups_query_active_groups_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)
            try:
                database.initialize_database()
                database.RandomSearchTag.create(
                    chat_id="active-a",
                    session_id="default:GroupMessage:active-a",
                    tag="tag-a",
                    is_suspended=False,
                )
                database.RandomSearchTag.create(
                    chat_id="active-b",
                    session_id="default:GroupMessage:active-b",
                    tag="tag-b",
                    is_suspended=False,
                )
                database.RandomSearchTag.create(
                    chat_id="suspended",
                    session_id="default:GroupMessage:suspended",
                    tag="tag-c",
                    is_suspended=True,
                )

                select_count = 0
                original_execute_sql = database.db.execute_sql

                def counting_execute_sql(sql, params=None, *args, **kwargs):
                    nonlocal select_count
                    if (
                        sql.strip().lower().startswith("select")
                        and "randomsearchtag" in sql.lower()
                    ):
                        select_count += 1
                    return original_execute_sql(sql, params, *args, **kwargs)

                database.db.execute_sql = counting_execute_sql
                try:
                    groups = database.get_all_random_search_groups()
                finally:
                    database.db.execute_sql = original_execute_sql
            finally:
                if not database.db.is_closed():
                    database.db.close()

            self.assertEqual(set(groups), {"active-a", "active-b"})
            self.assertEqual(select_count, 1)

    def test_random_ranking_groups_query_active_groups_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)
            try:
                database.initialize_database()
                database.RandomRankingConfig.create(
                    chat_id="active-a",
                    session_id="default:GroupMessage:active-a",
                    mode="day",
                    is_suspended=False,
                )
                database.RandomRankingConfig.create(
                    chat_id="active-b",
                    session_id="default:GroupMessage:active-b",
                    mode="week",
                    is_suspended=False,
                )
                database.RandomRankingConfig.create(
                    chat_id="suspended",
                    session_id="default:GroupMessage:suspended",
                    mode="month",
                    is_suspended=True,
                )

                select_count = 0
                original_execute_sql = database.db.execute_sql

                def counting_execute_sql(sql, params=None, *args, **kwargs):
                    nonlocal select_count
                    if (
                        sql.strip().lower().startswith("select")
                        and "randomrankingconfig" in sql.lower()
                    ):
                        select_count += 1
                    return original_execute_sql(sql, params, *args, **kwargs)

                database.db.execute_sql = counting_execute_sql
                try:
                    groups = database.get_all_random_ranking_groups()
                finally:
                    database.db.execute_sql = original_execute_sql
            finally:
                if not database.db.is_closed():
                    database.db.close()

            self.assertEqual(set(groups), {"active-a", "active-b"})
            self.assertEqual(select_count, 1)


if __name__ == "__main__":
    unittest.main()
