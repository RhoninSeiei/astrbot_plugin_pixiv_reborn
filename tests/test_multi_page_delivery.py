import asyncio
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


def fake_illust(page_count, illust_id=148023016):
    pages = []
    for index in range(page_count):
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
        self.pixiv_utils._temp_dir = None
        self.original_smart_clean = self.pixiv_utils.smart_clean_temp_dir
        self.pixiv_utils.smart_clean_temp_dir = self._noop_smart_clean

    async def asyncTearDown(self):
        self.pixiv_utils.smart_clean_temp_dir = self.original_smart_clean

    async def _noop_smart_clean(self, *args, **kwargs):
        pass

    async def _collect_delivery(self, illust, detail_message="details"):
        return [
            result
            async for result in self.pixiv_utils.send_pixiv_image(
                object(),
                FakeEvent(),
                illust,
                detail_message=detail_message,
                send_all_pages=True,
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

    async def test_nine_pages_yield_one_chain_with_all_images_and_details(self):
        results = await self._collect_delivery(fake_illust(9))

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakeMessageChain)
        images = [component for component in results[0].chain if isinstance(component, FakeImage)]
        details = [component for component in results[0].chain if isinstance(component, FakePlain)]
        self.assertEqual(len(images), 9)
        self.assertEqual([component.text for component in details], ["details"])

    async def test_ten_pages_yield_three_images_and_one_work_id(self):
        results = await self._collect_delivery(fake_illust(10))

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], FakeMessageChain)
        images = [component for component in results[0].chain if isinstance(component, FakeImage)]
        details = [component.text for component in results[0].chain if isinstance(component, FakePlain)]
        self.assertEqual(len(images), 3)
        self.assertEqual("\n".join(details).count("作品ID: 148023016"), 1)

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
            self.pixiv_utils._temp_dir = Path(__file__).resolve().parents[1] / ".tmp"
            results = await self._collect_delivery(fake_illust(2))

            self.assertEqual(len(results), 1)
            self.assertIsInstance(results[0], FakePlain)
            self.assertFalse(page_one_path.exists())
        finally:
            if page_one_path:
                page_one_path.unlink(missing_ok=True)
            self.pixiv_utils.download_image = original_download
            self.pixiv_utils._build_image_from_bytes = original_build


if __name__ == "__main__":
    unittest.main()
