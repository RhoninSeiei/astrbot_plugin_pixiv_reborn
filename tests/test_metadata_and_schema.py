import ast
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def install_config_import_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api


def read_simple_yaml(path):
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def read_info_dict_from_main():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PixivSearchPlugin":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "info":
                    for stmt in item.body:
                        if isinstance(stmt, ast.Return):
                            return ast.literal_eval(stmt.value)
    raise AssertionError("PixivSearchPlugin.info() not found")


class MetadataAndSchemaTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("utils.config", None)
        utils_module = sys.modules.get("utils")
        if utils_module is not None and hasattr(utils_module, "config"):
            delattr(utils_module, "config")

    def test_main_info_matches_metadata_yaml(self):
        metadata = read_simple_yaml(ROOT / "metadata.yaml")
        info = read_info_dict_from_main()

        self.assertEqual(info["name"], metadata["name"])
        self.assertEqual(info["author"], metadata["author"])
        self.assertEqual(info["version"], metadata["version"])
        self.assertEqual(info["homepage"], metadata["repo"])

    def test_command_config_schema_matches_webui_limits_for_shared_keys(self):
        install_config_import_stubs()
        from utils.config import PixivConfigManager

        webui_schema = json.loads(
            (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        command_schema = PixivConfigManager(object()).schema

        self.assertEqual(
            command_schema["return_count"]["max"],
            webui_schema["return_count"]["max"],
        )
        self.assertEqual(
            command_schema["random_sent_illust_retention_days"]["max"],
            webui_schema["random_sent_illust_retention_days"]["max"],
        )
        self.assertEqual(
            command_schema["random_search_empty_retry_extra_depth"]["max"],
            webui_schema["random_search_empty_retry_extra_depth"]["max"],
        )
        self.assertEqual(
            command_schema["random_search_max_concurrent_jobs"]["max"],
            webui_schema["random_search_max_concurrent_jobs"]["max"],
        )
        self.assertEqual(
            command_schema["pixiv_api_max_concurrent_requests"]["max"],
            webui_schema["pixiv_api_max_concurrent_requests"]["max"],
        )
        self.assertEqual(
            command_schema["pixiv_api_retry_count"]["max"],
            webui_schema["pixiv_api_retry_count"]["max"],
        )
        self.assertEqual(
            command_schema["pixiv_api_retry_base_delay"]["max"],
            webui_schema["pixiv_api_retry_base_delay"]["max"],
        )


if __name__ == "__main__":
    unittest.main()
