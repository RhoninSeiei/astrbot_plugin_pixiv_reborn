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


class RandomSearchClaimTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("utils.database", None)
        utils_module = sys.modules.get("utils")
        if utils_module is not None and hasattr(utils_module, "database"):
            delattr(utils_module, "database")

    def test_active_claim_blocks_duplicate_until_release_or_expiry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)

            database.initialize_database()

            now = datetime(2026, 5, 20, 12, 0, 0)
            first_claim = database.try_claim_random_search_execution(
                "905956314",
                now,
                now + timedelta(minutes=15),
            )
            duplicate_claim = database.try_claim_random_search_execution(
                "905956314",
                now + timedelta(minutes=1),
                now + timedelta(minutes=16),
            )

            self.assertTrue(first_claim)
            self.assertFalse(duplicate_claim)

            database.release_random_search_execution("905956314")
            self.assertTrue(
                database.try_claim_random_search_execution(
                    "905956314",
                    now + timedelta(minutes=2),
                    now + timedelta(minutes=17),
                )
            )

    def test_expired_claim_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = import_database_for_temp_dir(temp_dir)

            database.initialize_database()

            now = datetime(2026, 5, 20, 12, 0, 0)
            self.assertTrue(
                database.try_claim_random_search_execution(
                    "947135267",
                    now,
                    now + timedelta(minutes=5),
                )
            )
            self.assertTrue(
                database.try_claim_random_search_execution(
                    "947135267",
                    now + timedelta(minutes=6),
                    now + timedelta(minutes=21),
                )
            )


if __name__ == "__main__":
    unittest.main()
