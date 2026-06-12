import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASTRBOT_ROOT = PROJECT_ROOT / "AstrBot"
if str(ASTRBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ASTRBOT_ROOT))


fpdf_module = types.ModuleType("fpdf")
fpdf_module.FPDF = object
sys.modules.setdefault("fpdf", fpdf_module)


UTILS_MODULES_TO_RELOAD = (
    "utils.llm_tool",
    "utils.tag",
    "utils.pixiv_utils",
    "utils.random_empty_retry",
)


def clear_module_attrs(module_names):
    utils_module = sys.modules.get("utils")
    if utils_module is None:
        return
    for module_name in module_names:
        _, _, attr_name = module_name.partition(".")
        if attr_name and hasattr(utils_module, attr_name):
            delattr(utils_module, attr_name)


def restore_utils_attrs(module_names):
    utils_module = sys.modules.get("utils")
    if utils_module is None:
        return
    for module_name in module_names:
        _, _, attr_name = module_name.partition(".")
        if not attr_name:
            continue
        if module_name in sys.modules:
            setattr(utils_module, attr_name, sys.modules[module_name])
        elif hasattr(utils_module, attr_name):
            delattr(utils_module, attr_name)


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakePixivConfig:
    r18_mode = "过滤 R18"
    filter_r18g_only = False
    ai_filter_mode = "显示 AI 作品"
    ai_detection_mode = "field_or_tag"
    single_response_mode = False
    forward_threshold = False
    show_details = False


class FakeClientWrapper:
    async def authenticate(self):
        return True


class FakeUser:
    name = "artist"


class FakeIllust:
    def __init__(self, illust_id):
        self.id = illust_id
        self.title = f"title-{illust_id}"
        self.user = FakeUser()
        self.tags = []
        self.type = "illust"
        self.x_restrict = 0
        self.illust_ai_type = 0
        self.total_bookmarks = 1000 - illust_id
        self.total_view = 10000 + illust_id
        self.total_like = 500 + illust_id


class FakeSearchResult:
    def __init__(self, illusts, next_url=None):
        self.illusts = illusts
        self.next_url = next_url


class FakePixivClient:
    def __init__(self):
        self.calls = []

    def search_illust(self, word, **kwargs):
        self.calls.append((word, kwargs))
        if word == "Atlanta(艦隊これくしょん)":
            return FakeSearchResult([FakeIllust(1), FakeIllust(2), FakeIllust(3)])
        return FakeSearchResult([])

    def parse_qs(self, next_url):
        return {}


class FakeEvent:
    def __init__(self):
        self.sent = []

    async def send(self, result):
        self.sent.append(result)


class FakeAgentContext:
    def __init__(self, event):
        self.event = event


class PixivIllustSearchToolTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        module_names = list(sys.modules)
        cls.saved_modules = {
            name: sys.modules[name]
            for name in module_names
            if name == "astrbot"
            or name.startswith("astrbot.")
            or name in UTILS_MODULES_TO_RELOAD
        }
        for name in module_names:
            if (
                name == "astrbot"
                or name.startswith("astrbot.")
                or name in UTILS_MODULES_TO_RELOAD
            ):
                sys.modules.pop(name, None)
        clear_module_attrs(UTILS_MODULES_TO_RELOAD)

        from utils import llm_tool

        cls.llm_tool_module = llm_tool

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if (
                name == "astrbot"
                or name.startswith("astrbot.")
                or name in UTILS_MODULES_TO_RELOAD
            ):
                sys.modules.pop(name, None)
        sys.modules.update(cls.saved_modules)
        restore_utils_attrs(UTILS_MODULES_TO_RELOAD)

    def setUp(self):
        self.llm_tool = self.__class__.llm_tool_module
        self.original_logger = self.llm_tool.logger
        self.llm_tool.logger = FakeLogger()

    def tearDown(self):
        self.llm_tool.logger = self.original_logger

    def test_illust_tool_schema_accepts_agent_arguments(self):
        tool = self.llm_tool.PixivIllustSearchTool()

        props = tool.parameters["properties"]

        self.assertEqual(tool.name, "pixiv_search_illust")
        self.assertIn("query", props)
        self.assertIn("count", props)
        self.assertIn("filters", props)
        self.assertIn("tags", props)
        self.assertIn("source_text", props)
        self.assertEqual(props["count"]["maximum"], 5)
        self.assertEqual(props["filters"]["default"], "safe")

    def test_registered_tool_object_identity_is_shared_by_tool_sets(self):
        from astrbot.core.provider.func_tool_manager import FunctionToolManager

        tools = self.llm_tool.create_pixiv_llm_tools(
            pixiv_client=FakePixivClient(),
            pixiv_config=FakePixivConfig(),
            pixiv_client_wrapper=FakeClientWrapper(),
        )
        pixiv_tool = next(tool for tool in tools if tool.name == "pixiv_search_illust")
        manager = FunctionToolManager()
        manager.func_list.extend(tools)
        request_tool_set = manager.get_full_tool_set()

        self.assertIs(manager.get_func("pixiv_search_illust"), pixiv_tool)
        self.assertIs(request_tool_set.get_tool("pixiv_search_illust"), pixiv_tool)

    async def test_call_uses_candidate_tags_and_sends_images_in_event_context(self):
        from astrbot.core.agent.run_context import ContextWrapper

        client = FakePixivClient()
        event = FakeEvent()
        tool = self.llm_tool.PixivIllustSearchTool(
            pixiv_client=client,
            pixiv_config=FakePixivConfig(),
            pixiv_client_wrapper=FakeClientWrapper(),
        )

        async def fake_send_pixiv_image(client, event, illust, detail, show_details):
            yield types.SimpleNamespace(illust_id=illust.id)

        original_send = self.llm_tool.send_pixiv_image
        self.llm_tool.send_pixiv_image = fake_send_pixiv_image
        try:
            result = await tool.call(
                ContextWrapper(FakeAgentContext(event)),
                query="舰队收藏 亚特兰大",
                count=2,
                filters="safe",
                tags=["艦隊これくしょん", "Atlanta(艦隊これくしょん)"],
                source_text="来点舰队收藏亚特兰大图片",
            )
        finally:
            self.llm_tool.send_pixiv_image = original_send

        self.assertEqual(result, "已发送 2 张图片")
        self.assertEqual(client.calls[0][0], "Atlanta(艦隊これくしょん)")
        self.assertEqual([item.illust_id for item in event.sent], [1, 2])


if __name__ == "__main__":
    unittest.main()
