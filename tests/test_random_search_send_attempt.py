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


class RandomSearchSendAttemptTest(unittest.TestCase):
    def test_send_attempts_are_persisted_for_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import database

            database.initialize_database()

            database.add_random_search_send_attempt(
                chat_id="905956314",
                session_id="default:GroupMessage:905956314",
                source_type="tag",
                source_name="ビカラ",
                illust_id=123,
                success=True,
            )
            database.add_random_search_send_attempt(
                chat_id="947135267",
                session_id="qq2:GroupMessage:947135267",
                source_type="tag",
                source_name="エイメス",
                illust_id=456,
                success=False,
                error_message="rich media transfer failed",
            )

            rows = list(
                database.RandomSearchSendAttempt.select().order_by(
                    database.RandomSearchSendAttempt.id
                )
            )

            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0].success)
            self.assertEqual(rows[0].illust_id, 123)
            self.assertFalse(rows[1].success)
            self.assertEqual(rows[1].error_message, "rich media transfer failed")
            if not database.db.is_closed():
                database.db.close()


if __name__ == "__main__":
    unittest.main()
