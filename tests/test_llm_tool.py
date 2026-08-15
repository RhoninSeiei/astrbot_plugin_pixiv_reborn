import sys
import types
import unittest
from pathlib import Path


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = (
    WORKTREE_ROOT.parent.parent.parent
    if WORKTREE_ROOT.parent.name == ".worktrees"
    else WORKTREE_ROOT.parent
)
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
ASTRBOT_TEST_STUB_MODULES = (
    "deprecated",
    "jsonschema",
    "mcp",
    "mcp.types",
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


def install_astrbot_import_stubs():
    astrbot_module = types.ModuleType("astrbot")
    astrbot_module.__path__ = [str(ASTRBOT_ROOT / "astrbot")]
    core_module = types.ModuleType("astrbot.core")
    core_module.__path__ = [str(ASTRBOT_ROOT / "astrbot" / "core")]
    agent_module = types.ModuleType("astrbot.core.agent")
    agent_module.__path__ = [str(ASTRBOT_ROOT / "astrbot" / "core" / "agent")]
    message_module = types.ModuleType("astrbot.core.message")
    message_module.__path__ = [str(ASTRBOT_ROOT / "astrbot" / "core" / "message")]
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = types.SimpleNamespace()
    agent_context_module = types.ModuleType("astrbot.core.astr_agent_context")
    agent_context_module.AstrAgentContext = object
    message_result_module = types.ModuleType(
        "astrbot.core.message.message_event_result"
    )
    message_result_module.MessageEventResult = type("MessageEventResult", (), {})
    jsonschema_module = types.ModuleType("jsonschema")
    jsonschema_module.validate = lambda *args, **kwargs: None
    jsonschema_module.Draft202012Validator = types.SimpleNamespace(META_SCHEMA={})
    deprecated_module = types.ModuleType("deprecated")
    deprecated_module.deprecated = lambda **kwargs: lambda func: func
    mcp_module = types.ModuleType("mcp")
    mcp_types_module = types.ModuleType("mcp.types")
    mcp_types_module.CallToolResult = type("CallToolResult", (), {})
    mcp_module.types = mcp_types_module
    pixiv_utils_module = types.ModuleType("utils.pixiv_utils")

    async def send_pixiv_image(*args, **kwargs):
        return None

    pixiv_utils_module.send_pixiv_image = send_pixiv_image
    pixiv_utils_module.generate_safe_filename = lambda title, default_name: title

    sys.modules.update(
        {
            "astrbot": astrbot_module,
            "astrbot.core": core_module,
            "astrbot.core.agent": agent_module,
            "astrbot.core.message": message_module,
            "astrbot.api": api_module,
            "astrbot.core.astr_agent_context": agent_context_module,
            "astrbot.core.message.message_event_result": message_result_module,
            "deprecated": deprecated_module,
            "jsonschema": jsonschema_module,
            "mcp": mcp_module,
            "mcp.types": mcp_types_module,
            "utils.pixiv_utils": pixiv_utils_module,
        }
    )


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
    def __init__(self):
        self.calls = []

    async def authenticate(self):
        return True

    async def call_pixiv_api(self, func, *args, **kwargs):
        self.calls.append((getattr(func, "__name__", repr(func)), args, kwargs))
        return func(*args, **kwargs)


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
            or name in ASTRBOT_TEST_STUB_MODULES
        }
        for name in module_names:
            if (
                name == "astrbot"
                or name.startswith("astrbot.")
                or name in UTILS_MODULES_TO_RELOAD
                or name in ASTRBOT_TEST_STUB_MODULES
            ):
                sys.modules.pop(name, None)
        clear_module_attrs(UTILS_MODULES_TO_RELOAD)
        install_astrbot_import_stubs()

        from utils import llm_tool

        cls.llm_tool_module = llm_tool

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if (
                name == "astrbot"
                or name.startswith("astrbot.")
                or name in UTILS_MODULES_TO_RELOAD
                or name in ASTRBOT_TEST_STUB_MODULES
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

    async def test_registered_tools_remain_native_and_permission_proxy_delegates_call(
        self,
    ):
        from astrbot.core.agent.tool import ToolSet

        FunctionTool = self.llm_tool.FunctionTool

        class PermissionGuardedToolV4273Stub(FunctionTool):
            def __init__(self, tool, manager):
                super().__init__(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                )
                self._wrapped = tool
                self._mgr = manager
                self.active = getattr(tool, "active", True)
                self.handler_module_path = getattr(tool, "handler_module_path", None)

            async def call(self, context, **kwargs):
                error = await self._mgr._check_tool_permission(self.name, context)
                if error is not None:
                    return error
                if self._wrapped.handler is not None:
                    return await self._wrapped.handler(context.context.event, **kwargs)
                return await self._wrapped.call(context, **kwargs)

        class FunctionToolManagerV4273Stub:
            def __init__(self):
                self.func_list = []
                self.permission_error = None
                self.permission_checks = []

            async def _check_tool_permission(self, name, context):
                self.permission_checks.append((name, context))
                return self.permission_error

            def get_full_tool_set(self):
                tool_set = ToolSet()
                for tool in self.func_list:
                    tool_set.add_tool(PermissionGuardedToolV4273Stub(tool, self))
                return tool_set

        tools = self.llm_tool.create_pixiv_llm_tools()
        pixiv_tool = next(tool for tool in tools if tool.name == "pixiv_search_illust")
        manager = FunctionToolManagerV4273Stub()
        manager.func_list.extend(tools)
        request_tool_set = manager.get_full_tool_set()
        guarded_tool = request_tool_set.get_tool("pixiv_search_illust")
        request = types.SimpleNamespace(func_tool=request_tool_set)
        run_context = self.llm_tool.ContextWrapper(FakeAgentContext(FakeEvent()))

        result = await guarded_tool.call(run_context, query="test")
        self.assertEqual(result, "错误: Pixiv客户端未初始化")
        self.assertEqual(manager.permission_checks, [(pixiv_tool.name, run_context)])
        self.assertIs(request.func_tool.get_tool(pixiv_tool.name), guarded_tool)
        self.assertIs(guarded_tool._wrapped, pixiv_tool)
        self.assertIsInstance(pixiv_tool, self.llm_tool.PixivIllustSearchTool)
        self.assertIsNone(pixiv_tool.handler)

        manager.permission_error = "permission denied"
        self.assertEqual(
            await guarded_tool.call(run_context, query="test"),
            "permission denied",
        )
        self.assertEqual(len(manager.permission_checks), 2)

    def test_registered_tools_are_bound_to_plugin_main_module(self):
        tools = self.llm_tool.create_pixiv_llm_tools(
            pixiv_client=FakePixivClient(),
            pixiv_config=FakePixivConfig(),
            pixiv_client_wrapper=FakeClientWrapper(),
        )
        module_path = "data.plugins.astrbot_plugin_pixiv_reborn.main"

        self.llm_tool.bind_tools_to_plugin_module(tools, module_path)

        for tool in tools:
            self.assertEqual(tool.__module__, module_path)
            self.assertEqual(tool.handler_module_path, module_path)
            self.assertIsNone(tool.handler)

    async def test_call_uses_candidate_tags_and_sends_images_in_event_context(self):
        from astrbot.core.agent.run_context import ContextWrapper

        client = FakePixivClient()
        wrapper = FakeClientWrapper()
        event = FakeEvent()
        tool = self.llm_tool.PixivIllustSearchTool(
            pixiv_client=client,
            pixiv_config=FakePixivConfig(),
            pixiv_client_wrapper=wrapper,
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
        self.assertEqual([call[0] for call in wrapper.calls], ["search_illust"])
        self.assertEqual([item.illust_id for item in event.sent], [1, 2])


if __name__ == "__main__":
    unittest.main()
