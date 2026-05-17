import asyncio
import sys
import tempfile
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
    def __init__(self, *, path=None, file=None, url=None, **kwargs):
        self.path = path
        self.file = file
        self.url = url
        for key, value in kwargs.items():
            setattr(self, key, value)

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return FakeImage(path=path, file=f"file:///{Path(path).absolute()}", **kwargs)

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

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["pixivpy3"] = pixivpy3


class PixivTempCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_file_send_temp_image_is_removed_after_message_cleanup(self):
        install_import_stubs()
        from utils import pixiv_utils

        with tempfile.TemporaryDirectory() as temp_dir:
            pixiv_utils._temp_dir = Path(temp_dir)
            pixiv_utils._config = types.SimpleNamespace(
                image_send_method="file",
                pil_compress_quality=100,
                pil_compress_target_kb=0,
            )

            image = await pixiv_utils._build_image_from_bytes(b"image-bytes")
            self.assertTrue(Path(image.path).exists())

            await pixiv_utils.cleanup_pixiv_temp_files(FakeMessageChain([image]))

            self.assertFalse(Path(image.path).exists())


if __name__ == "__main__":
    unittest.main()
