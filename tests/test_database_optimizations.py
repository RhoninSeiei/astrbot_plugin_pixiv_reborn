import sys
import tempfile
import types
import unittest
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
