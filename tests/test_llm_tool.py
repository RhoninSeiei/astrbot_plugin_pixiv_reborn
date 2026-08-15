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
        }
    )


def install_pixiv_utils_stub():
    pixiv_utils_module = types.ModuleType("utils.pixiv_utils")

    async def send_pixiv_image(*args, **kwargs):
        return None

    pixiv_utils_module.send_pixiv_image = send_pixiv_image
    pixiv_utils_module.generate_safe_filename = lambda title, default_name: title
    sys.modules["utils.pixiv_utils"] = pixiv_utils_module


def load_v4273_runtime():
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.provider.func_tool_manager import (
        FunctionToolManager,
        _PermissionGuardedTool,
    )

    return types.SimpleNamespace(
        ContextWrapper=ContextWrapper,
        FunctionToolExecutor=FunctionToolExecutor,
        FunctionToolManager=FunctionToolManager,
        PermissionGuardedTool=_PermissionGuardedTool,
    )


def is_astrbot_module(name):
    return name == "astrbot" or name.startswith("astrbot.")


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
        try:
            cls.v4273_runtime = load_v4273_runtime()
        except (ImportError, AttributeError):
            cls.v4273_runtime = None
            install_astrbot_import_stubs()
        install_pixiv_utils_stub()

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

    def test_factory_returns_native_tools_without_handlers(self):
        tools = self.llm_tool.create_pixiv_llm_tools()
        self.assertIsInstance(tools[0], self.llm_tool.PixivIllustSearchTool)
        self.assertIsInstance(tools[1], self.llm_tool.PixivNovelSearchTool)
        self.assertTrue(all(tool.handler is None for tool in tools))

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


class PixivV4273NativeToolTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        module_names = list(sys.modules)
        cls.saved_astrbot_modules = {
            name: sys.modules[name] for name in module_names if is_astrbot_module(name)
        }
        for name in module_names:
            if is_astrbot_module(name):
                sys.modules.pop(name, None)

        try:
            v4273_runtime = load_v4273_runtime()
        except (ImportError, AttributeError) as exc:
            cls.runtime = None
            cls.skip_reason = f"AstrBot v4.27.3 runtime unavailable: {exc}"
            return

        module_names = list(sys.modules)
        cls.saved_utils_modules = {
            name: sys.modules[name]
            for name in module_names
            if name in UTILS_MODULES_TO_RELOAD
        }
        for name in module_names:
            if name in UTILS_MODULES_TO_RELOAD:
                sys.modules.pop(name, None)
        clear_module_attrs(UTILS_MODULES_TO_RELOAD)
        install_pixiv_utils_stub()

        from utils import llm_tool

        v4273_runtime.llm_tool = llm_tool
        cls.runtime = v4273_runtime

    @classmethod
    def tearDownClass(cls):
        if cls.runtime is None:
            sys.modules.update(cls.saved_astrbot_modules)
            return
        for name in list(sys.modules):
            if name in UTILS_MODULES_TO_RELOAD:
                sys.modules.pop(name, None)
        sys.modules.update(cls.saved_utils_modules)
        for name in list(sys.modules):
            if is_astrbot_module(name):
                sys.modules.pop(name, None)
        sys.modules.update(cls.saved_astrbot_modules)
        restore_utils_attrs(UTILS_MODULES_TO_RELOAD)

    def setUp(self):
        if self.runtime is None:
            self.skipTest(self.skip_reason)
        self.llm_tool = self.runtime.llm_tool
        self.original_logger = self.llm_tool.logger
        self.llm_tool.logger = FakeLogger()

    def tearDown(self):
        if self.runtime is not None:
            self.llm_tool.logger = self.original_logger

    async def _execute(self, tool, context, **tool_args):
        return [
            result
            async for result in self.runtime.FunctionToolExecutor._execute_local(
                tool,
                context,
                **tool_args,
            )
        ]

    def _install_permission_check(self, manager):
        state = types.SimpleNamespace(error=None, calls=[])

        async def check_permission(name, context):
            state.calls.append((name, context))
            return state.error

        manager._check_tool_permission = check_permission
        return state

    async def test_illust_proxy_executes_native_call_with_context_and_permission(self):
        tools = self.llm_tool.create_pixiv_llm_tools()
        native_tool = next(tool for tool in tools if tool.name == "pixiv_search_illust")
        manager = self.runtime.FunctionToolManager()
        permission = self._install_permission_check(manager)
        manager.func_list.extend(tools)
        proxy = manager.get_full_tool_set().get_tool(native_tool.name)
        context = self.runtime.ContextWrapper(FakeAgentContext(FakeEvent()))
        original_call = native_tool.call
        received_contexts = []

        async def record_call(call_context, **kwargs):
            received_contexts.append((call_context, kwargs))
            return await original_call(call_context, **kwargs)

        object.__setattr__(native_tool, "call", record_call)
        results = await self._execute(proxy, context, query="test")

        self.assertIs(manager.func_list[0], native_tool)
        self.assertIsInstance(proxy, self.runtime.PermissionGuardedTool)
        self.assertIs(proxy._wrapped, native_tool)
        self.assertIsNone(native_tool.handler)
        self.assertIsNone(proxy.handler)
        self.assertEqual(results[0].content[0].text, "错误: Pixiv客户端未初始化")
        self.assertEqual(received_contexts, [(context, {"query": "test"})])
        self.assertEqual(permission.calls, [(native_tool.name, context)])

        permission.error = "permission denied"
        denied_results = await self._execute(proxy, context, query="test")

        self.assertEqual(denied_results[0].content[0].text, "permission denied")
        self.assertEqual(len(received_contexts), 1)

    async def test_novel_proxy_forwards_keyword_and_id_with_event_and_missing_event(self):
        class NovelClient:
            def __init__(self):
                self.calls = []
                self.novel = types.SimpleNamespace(
                    id=42,
                    title="novel-title",
                    user=FakeUser(),
                )

            def search_novel(self, query, **kwargs):
                self.calls.append(("search_novel", query, kwargs))
                return types.SimpleNamespace(novels=[self.novel])

            def novel_detail(self, novel_id):
                self.calls.append(("novel_detail", novel_id, {}))
                return types.SimpleNamespace(novel=self.novel)

        client = NovelClient()
        tools = self.llm_tool.create_pixiv_llm_tools(pixiv_client=client)
        native_tool = next(tool for tool in tools if tool.name == "pixiv_search_novel")
        manager = self.runtime.FunctionToolManager()
        self._install_permission_check(manager)
        manager.func_list.extend(tools)
        proxy = manager.get_full_tool_set().get_tool(native_tool.name)
        event = FakeEvent()
        context = self.runtime.ContextWrapper(FakeAgentContext(event))
        received_contexts = []
        original_call = native_tool.call

        async def record_call(call_context, **kwargs):
            received_contexts.append((call_context, kwargs))
            return await original_call(call_context, **kwargs)

        async def send_novel_result(received_event, items, query, tags):
            self.assertIs(received_event, event)
            self.assertEqual(items, [client.novel])
            return f"event result for {query}"

        object.__setattr__(native_tool, "call", record_call)
        native_tool._send_novel_result = send_novel_result
        keyword_results = await self._execute(proxy, context, query="keyword")
        missing_event_context = self.runtime.ContextWrapper(FakeAgentContext(None))
        missing_event_result = await proxy.call(missing_event_context, query="42")

        self.assertEqual(keyword_results[0].content[0].text, "event result for keyword")
        self.assertEqual(
            missing_event_result,
            "找到小说: novel-title (ID: 42)，但无法发送文件(无事件上下文)。",
        )
        self.assertEqual(
            received_contexts,
            [(context, {"query": "keyword"}), (missing_event_context, {"query": "42"})],
        )
        self.assertEqual(
            client.calls,
            [
                ("search_novel", "keyword", {"search_target": "partial_match_for_tags"}),
                ("novel_detail", 42, {}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
