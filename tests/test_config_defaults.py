import asyncio
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


class RecordingConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0

    def save_config(self):
        self.save_calls += 1


def install_import_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api


class PixivConfigDefaultsTest(unittest.TestCase):
    def test_command_update_normalizes_persists_and_reloads_exclusions(self):
        install_import_stubs()
        from utils.config import PixivConfig, PixivConfigManager

        stored_config = RecordingConfig()
        config = PixivConfig(stored_config)
        manager = PixivConfigManager(config)

        success, _ = manager.validate_and_set_config(
            "automatic_push_excluded_tags", " NTR，悪堕ち、ntr "
        )

        self.assertTrue(success)
        self.assertEqual(config.automatic_push_excluded_tags, ["ntr", "悪堕ち"])
        self.assertEqual(stored_config["automatic_push_excluded_tags"], "ntr,悪堕ち")
        self.assertEqual(stored_config.save_calls, 1)
        self.assertEqual(
            PixivConfig(stored_config).automatic_push_excluded_tags,
            ["ntr", "悪堕ち"],
        )

    def test_command_empty_value_disables_exclusions_while_omission_queries(self):
        install_import_stubs()
        from utils.config import PixivConfig, PixivConfigManager

        stored_config = RecordingConfig()
        config = PixivConfig(stored_config)
        manager = PixivConfigManager(config)

        query_result = asyncio.run(
            manager.handle_config_command(None, "automatic_push_excluded_tags", None)
        )
        update_result = asyncio.run(
            manager.handle_config_command(None, "automatic_push_excluded_tags", "")
        )

        self.assertIn("automatic_push_excluded_tags 当前值", query_result)
        self.assertIn("automatic_push_excluded_tags 已更新为: []", update_result)
        self.assertEqual(config.automatic_push_excluded_tags, [])
        self.assertEqual(stored_config["automatic_push_excluded_tags"], "")
        self.assertEqual(stored_config.save_calls, 1)

    def test_current_config_and_info_expose_normalized_exclusions(self):
        install_import_stubs()
        from utils.config import PixivConfig, PixivConfigManager

        config = PixivConfig({"automatic_push_excluded_tags": "NTR,悪堕ち"})
        manager = PixivConfigManager(config)

        self.assertEqual(
            manager.get_current_config()["automatic_push_excluded_tags"],
            ["ntr", "悪堕ち"],
        )
        self.assertIn(
            "automatic_push_excluded_tags=['ntr', '悪堕ち']",
            config.get_config_info(),
        )
        self.assertIn(
            "automatic_push_excluded_tags 当前值",
            manager.get_param_info("automatic_push_excluded_tags"),
        )

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

    def test_llm_tool_sent_retention_defaults_to_45_days(self):
        install_import_stubs()
        from utils.config import PixivConfig

        self.assertEqual(PixivConfig({}).llm_tool_sent_illust_retention_days, 45)


if __name__ == "__main__":
    unittest.main()
