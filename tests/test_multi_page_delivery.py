import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakeImage:
    def __init__(self, *, path=None, url=None, data=None):
        self.path = path
        self.url = url
        self.data = data

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return FakeImage(path=path)

    @staticmethod
    def fromBytes(data):
        return FakeImage(data=data)

    @staticmethod
    def fromURL(url):
        return FakeImage(url=url)


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeMessageChain:
    def __init__(self, chain):
        self.chain = chain


class FakeEvent:
    def chain_result(self, components):
        return FakeMessageChain(components)

    def plain_result(self, text):
        return FakePlain(text)


class FakeClientSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        pass


class FakeAsyncFile:
    def __init__(self, path, mode):
        self.file = open(path, mode)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.file.close()

    async def write(self, data):
        return self.file.write(data)


def fake_aiofiles_open(path, mode, **kwargs):
    return FakeAsyncFile(path, mode)


def install_import_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()
    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.Image = FakeImage
    message_components.Plain = FakePlain
    message_components.Node = object
    message_components.Nodes = object
    pixivpy3 = types.ModuleType("pixivpy3")
    pixivpy3.AppPixivAPI = object
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = FakeClientSession
    aiohttp.ClientTimeout = lambda **kwargs: object()
    aiofiles = types.ModuleType("aiofiles")
    aiofiles.open = fake_aiofiles_open

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["pixivpy3"] = pixivpy3
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiofiles"] = aiofiles


def fake_illust(page_count, illust_id=148023016, available_page_count=None):
    pages = []
    for index in range(
        page_count if available_page_count is None else available_page_count
    ):
        pages.append(
            types.SimpleNamespace(
                image_urls=types.SimpleNamespace(
                    original=f"https://example.test/{index}.jpg",
                    large=f"https://example.test/{index}-large.jpg",
                    medium=f"https://example.test/{index}-medium.jpg",
                )
            )
        )
    return types.SimpleNamespace(
        id=illust_id,
        type="illust",
        page_count=page_count,
        meta_pages=pages,
        meta_single_page=types.SimpleNamespace(
            original_image_url="https://example.test/single.jpg"
        ),
        image_urls=types.SimpleNamespace(
            large="https://example.test/single-large.jpg",
            medium="https://example.test/single-medium.jpg",
        ),
    )


def fake_ugoira(illust_id=148023016):
    return types.SimpleNamespace(
        id=illust_id,
        type="ugoira",
        title="animated work",
        user=types.SimpleNamespace(name="artist"),
        page_count=12,
        meta_pages=[],
    )


def load_illust_handler_module():
    module_name = "task_five_handler.handlers.illust"
    package = types.ModuleType("task_five_handler")
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    handlers_package = types.ModuleType("task_five_handler.handlers")
    handlers_package.__path__ = [str(Path(__file__).resolve().parents[1] / "handlers")]
    utils_package = types.ModuleType("task_five_handler.utils")
    utils_package.__path__ = [str(Path(__file__).resolve().parents[1] / "utils")]

    astrbot_event = types.ModuleType("astrbot.api.event")
    astrbot_event.AstrMessageEvent = object
    tag = types.ModuleType("task_five_handler.utils.tag")
    tag.build_detail_message = lambda illust, is_novel=False: "details"
    tag.FilterConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
    tag.validate_and_process_tags = lambda tags: {"success": True}
    tag.process_and_send_illusts = None
    tag.filter_illusts_with_reason = lambda illusts, config: (illusts, [])
    tag.process_and_send_illusts_sorted = None
    pixiv_utils = types.ModuleType("task_five_handler.utils.pixiv_utils")
    pixiv_utils.send_pixiv_image = None
    pixiv_utils.send_forward_message = None
    help_module = types.ModuleType("task_five_handler.utils.help")
    help_module.get_help_message = lambda *args, **kwargs: "help"

    sys.modules["astrbot.api.event"] = astrbot_event
    sys.modules["task_five_handler"] = package
    sys.modules["task_five_handler.handlers"] = handlers_package
    sys.modules["task_five_handler.utils"] = utils_package
    sys.modules["task_five_handler.utils.tag"] = tag
    sys.modules["task_five_handler.utils.pixiv_utils"] = pixiv_utils
    sys.modules["task_five_handler.utils.help"] = help_module
    sys.modules.pop(module_name, None)

    module_path = Path(__file__).resolve().parents[1] / "handlers" / "illust.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    handler = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = handler
    spec.loader.exec_module(handler)
    return handler


class MultiPageDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_import_stubs()
        from utils import pixiv_utils

        self.pixiv_utils = pixiv_utils
        self.pixiv_utils._config = types.SimpleNamespace(
            image_send_method="url",
            image_quality="original",
            use_image_proxy=False,
            proxy="",
        )
        self.temp_dir = Path(__file__).resolve().parents[1] / ".tmp"
        self.created_temp_dir = not self.temp_dir.exists()
        self.temp_dir.mkdir(exist_ok=True)
        self.pixiv_utils._temp_dir = self.temp_dir
        self.original_smart_clean = self.pixiv_utils.smart_clean_temp_dir
        self.pixiv_utils.smart_clean_temp_dir = self._noop_smart_clean

    async def asyncTearDown(self):
        self.pixiv_utils.smart_clean_temp_dir = self.original_smart_clean
        for path in self.temp_dir.glob("pixiv_*"):
            path.unlink(missing_ok=True)
        if self.created_temp_dir:
            self.temp_dir.rmdir()

    async def _noop_smart_clean(self, *args, **kwargs):
        pass

    async def _collect_delivery(
        self, illust, detail_message="details", send_all_pages=True
    ):
        return [
            result
            async for result in self.pixiv_utils.send_pixiv_image(
                object(),
                FakeEvent(),
                illust,
                detail_message=detail_message,
                send_all_pages=send_all_pages,
            )
        ]

    def test_delivery_image_count_boundaries(self):
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(1), True), 1
        )
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(2), True), 2
        )
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(9), True), 9
        )
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(10), True), 3
        )
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(20), True), 3
        )
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_illust(9), False), 1
        )

    def test_ugoira_delivery_count_is_one(self):
        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(fake_ugoira(), True), 1
        )

    async def test_ugoira_delegates_once_without_normal_page_selection(self):
        calls = []
        original_send_ugoira = self.pixiv_utils.send_ugoira
        original_select = self.pixiv_utils._select_illust_url_sources

        async def fake_send_ugoira(client, event, illust, detail_message, show_details=True):
            calls.append((illust.id, detail_message, show_details))
            yield event.chain_result([FakeImage(data=b"delegated")])

        def fail_normal_page_selection(*args, **kwargs):
            raise AssertionError("ugoira must not use normal page selection")

        self.pixiv_utils.send_ugoira = fake_send_ugoira
        self.pixiv_utils._select_illust_url_sources = fail_normal_page_selection
        try:
            results = await self._collect_delivery(fake_ugoira())
        finally:
            self.pixiv_utils.send_ugoira = original_send_ugoira
            self.pixiv_utils._select_illust_url_sources = original_select

        self.assertEqual(calls, [(148023016, "details", True)])
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0].chain[0], FakeImage)

    async def test_ugoira_success_yields_one_gif_image_and_details(self):
        original_process = self.pixiv_utils.process_ugoira_for_content
        original_build = self.pixiv_utils._build_image_from_bytes
        build_calls = []

        async def successful_process(client, session, illust, detail_message=None):
            return {"gif_data": b"GIF89a", "ugoira_info": "ugoira details"}

        async def capture_build(data, ext=".jpg"):
            build_calls.append((data, ext))
            return FakeImage(data=data)

        self.pixiv_utils.process_ugoira_for_content = successful_process
        self.pixiv_utils._build_image_from_bytes = capture_build
        try:
            results = [
                result
                async for result in self.pixiv_utils.send_ugoira(
                    object(), FakeEvent(), fake_ugoira(), "details"
                )
            ]
        finally:
            self.pixiv_utils.process_ugoira_for_content = original_process
            self.pixiv_utils._build_image_from_bytes = original_build

        self.assertEqual(build_calls, [(b"GIF89a", ".gif")])
        self.assertEqual(len(results), 1)
        self.assertEqual(
            [type(component) for component in results[0].chain],
            [FakeImage, FakePlain],
        )
        self.assertEqual(results[0].chain[1].text, "ugoira details")

    async def test_ugoira_failure_yields_readable_failure_notice(self):
        original_process = self.pixiv_utils.process_ugoira_for_content

        async def failed_process(client, session, illust, detail_message=None):
            return None

        self.pixiv_utils.process_ugoira_for_content = failed_process
        try:
            results = [
                result
                async for result in self.pixiv_utils.send_ugoira(
                    object(), FakeEvent(), fake_ugoira(), "details"
                )
            ]
        finally:
            self.pixiv_utils.process_ugoira_for_content = original_process

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakePlain)
        self.assertEqual(results[0].text, "动图处理失败")

    async def test_nine_pages_yield_one_chain_with_all_images_and_details(self):
        results = await self._collect_delivery(fake_illust(9))

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakeMessageChain)
        images = [component for component in results[0].chain if isinstance(component, FakeImage)]
        details = [component for component in results[0].chain if isinstance(component, FakePlain)]
        self.assertEqual(len(images), 9)
        self.assertEqual(
            [component.url for component in images],
            [f"https://example.test/{index}.jpg" for index in range(9)],
        )
        self.assertEqual([component.text for component in details], ["details"])

    async def test_ten_pages_yield_three_images_and_one_work_id(self):
        results = await self._collect_delivery(fake_illust(10))

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakeMessageChain)
        images = [component for component in results[0].chain if isinstance(component, FakeImage)]
        details = [component.text for component in results[0].chain if isinstance(component, FakePlain)]
        self.assertEqual(len(images), 3)
        self.assertEqual(
            [component.url for component in images],
            ["https://example.test/0.jpg", "https://example.test/1.jpg", "https://example.test/2.jpg"],
        )
        self.assertEqual("\n".join(details).count("作品ID: 148023016"), 1)

    async def test_ten_page_default_delivery_does_not_append_work_id(self):
        results = await self._collect_delivery(
            fake_illust(10), send_all_pages=False
        )

        details = [
            component.text for component in results[0].chain if isinstance(component, FakePlain)
        ]
        self.assertEqual(details, ["details"])

    async def test_missing_meta_page_fails_the_whole_delivery(self):
        illust = fake_illust(3, available_page_count=2)
        results = await self._collect_delivery(illust)

        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(illust, True), 3
        )
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakePlain)
        self.assertIn("图片下载失败", results[0].text)

    async def test_empty_meta_pages_fail_without_an_image_message(self):
        illust = fake_illust(3, available_page_count=0)
        results = await self._collect_delivery(illust)

        self.assertEqual(
            self.pixiv_utils.get_illust_delivery_image_count(illust, True), 3
        )
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakePlain)
        self.assertIn("图片下载失败", results[0].text)

    async def test_existing_work_id_is_not_duplicated_for_truncated_delivery(self):
        results = await self._collect_delivery(
            fake_illust(10), "details\n作品ID: 148023016"
        )

        details = [
            component.text for component in results[0].chain if isinstance(component, FakePlain)
        ]
        self.assertEqual("\n".join(details).count("作品ID: 148023016"), 1)

    async def test_url_delivery_does_not_create_a_client_session(self):
        original_session = self.pixiv_utils.aiohttp.ClientSession

        def fail_client_session():
            raise AssertionError("URL delivery must not create a client session")

        self.pixiv_utils.aiohttp.ClientSession = fail_client_session
        try:
            results = await self._collect_delivery(fake_illust(1))
        finally:
            self.pixiv_utils.aiohttp.ClientSession = original_session

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chain[0].url, "https://example.test/single.jpg")

    async def test_file_mode_failure_cleans_built_page_and_yields_only_failure(self):
        self.pixiv_utils._config.image_send_method = "file"
        page_one_path = None
        original_download = self.pixiv_utils.download_image
        original_build = self.pixiv_utils._build_image_from_bytes

        async def fake_download(session, url, headers=None):
            return b"page-one" if url.endswith("/0.jpg") else None

        async def fake_build(data, ext=".jpg"):
            nonlocal page_one_path
            page_one_path = Path(self.pixiv_utils._temp_dir) / "pixiv_page_one.jpg"
            page_one_path.write_bytes(data)
            return FakeImage(path=str(page_one_path))

        self.pixiv_utils.download_image = fake_download
        self.pixiv_utils._build_image_from_bytes = fake_build
        try:
            results = await self._collect_delivery(fake_illust(2))

            self.assertEqual(len(results), 1)
            self.assertIsInstance(results[0], FakePlain)
            self.assertFalse(page_one_path.exists())
        finally:
            if page_one_path:
                page_one_path.unlink(missing_ok=True)
            self.pixiv_utils.download_image = original_download
            self.pixiv_utils._build_image_from_bytes = original_build

    async def test_component_construction_failure_cleans_its_written_temp_files(self):
        self.pixiv_utils._config.image_send_method = "file"
        original_from_file = FakeImage.__dict__["fromFileSystem"]
        original_download = self.pixiv_utils.download_image

        def fail_from_file_system(path, **kwargs):
            raise RuntimeError("component construction failed")

        async def successful_download(session, url, headers=None):
            return b"image-bytes"

        FakeImage.fromFileSystem = staticmethod(fail_from_file_system)
        self.pixiv_utils.download_image = successful_download
        try:
            results = await self._collect_delivery(fake_illust(1))
        finally:
            FakeImage.fromFileSystem = original_from_file
            self.pixiv_utils.download_image = original_download

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakePlain)
        self.assertEqual(list(self.temp_dir.glob("pixiv_*")), [])

    async def test_specific_id_uses_atomic_sender_when_forwarding_is_enabled(self):
        handler_module = load_illust_handler_module()
        captured_kwargs = {}
        forward_calls = []

        async def fake_send_pixiv_image(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield args[1].plain_result("sent")

        async def fail_send_forward_message(*args, **kwargs):
            forward_calls.append((args, kwargs))
            yield None

        class FakeClientWrapper:
            client_api = types.SimpleNamespace(illust_detail=object())

            async def authenticate(self):
                return True

            async def call_pixiv_api(self, method, illust_id):
                return types.SimpleNamespace(illust=fake_illust(2))

        handler_module.send_pixiv_image = fake_send_pixiv_image
        handler_module.send_forward_message = fail_send_forward_message
        handler = handler_module.IllustHandler(
            FakeClientWrapper(),
            types.SimpleNamespace(
                r18_mode="filter",
                filter_r18g_only=False,
                ai_filter_mode="show",
                ai_detection_mode="field_or_tag",
                return_count=1,
                show_filter_result=False,
                single_response_mode=False,
                show_details=True,
                forward_threshold=True,
            ),
        )

        results = [result async for result in handler.pixiv_specific(FakeEvent(), "42")]

        self.assertEqual(len(results), 1)
        self.assertTrue(captured_kwargs["send_all_pages"])
        self.assertEqual(forward_calls, [])

    async def test_partial_file_write_failure_cleans_its_temp_files(self):
        self.pixiv_utils._config.image_send_method = "file"
        original_open = self.pixiv_utils.aiofiles.open
        original_download = self.pixiv_utils.download_image

        class PartialWriteFile:
            def __init__(self, path, mode):
                self.file = open(path, mode)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                self.file.close()

            async def write(self, data):
                self.file.write(data[:1])
                self.file.flush()
                raise OSError("partial write failed")

        def partial_write_open(path, mode, **kwargs):
            return PartialWriteFile(path, mode)

        async def successful_download(session, url, headers=None):
            return b"image-bytes"

        self.pixiv_utils.aiofiles.open = partial_write_open
        self.pixiv_utils.download_image = successful_download
        try:
            results = await self._collect_delivery(fake_illust(1))
        finally:
            self.pixiv_utils.aiofiles.open = original_open
            self.pixiv_utils.download_image = original_download

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakePlain)
        self.assertEqual(list(self.temp_dir.glob("pixiv_*")), [])


if __name__ == "__main__":
    unittest.main()
