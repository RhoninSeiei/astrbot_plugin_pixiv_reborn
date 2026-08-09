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
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api


class PixivConfigDefaultsTest(unittest.TestCase):
    def test_automatic_push_exclusions_default_to_required_tags(self):
        install_import_stubs()
        from utils.config import PixivConfig

        self.assertEqual(
            PixivConfig({}).automatic_push_excluded_tags,
            ["ntr", "悪堕ち"],
        )

    def test_automatic_push_exclusions_can_be_disabled(self):
        install_import_stubs()
        from utils.config import PixivConfig

        self.assertEqual(
            PixivConfig({"automatic_push_excluded_tags": ""}).automatic_push_excluded_tags,
            [],
        )

    def test_subscription_force_forward_defaults_to_direct_message(self):
        install_import_stubs()
        from utils.config import PixivConfig

        self.assertFalse(PixivConfig({}).subscription_force_forward)
        self.assertTrue(
            PixivConfig({"subscription_force_forward": True}).subscription_force_forward
        )


if __name__ == "__main__":
    unittest.main()
