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


class FakeScheduler:
    running = False

    def add_job(self, *args, **kwargs):
        return object()

    def start(self):
        self.running = True

    def shutdown(self):
        self.running = False


class FakeIllust:
    def __init__(self, illust_id):
        self.id = illust_id


class FakeClient:
    def user_illusts(self, target_id):
        return types.SimpleNamespace(illusts=[FakeIllust(2)])


def load_subscription_module():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()

    apscheduler = types.ModuleType("apscheduler")
    apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
    apscheduler_asyncio = types.ModuleType("apscheduler.schedulers.asyncio")
    apscheduler_asyncio.AsyncIOScheduler = lambda *args, **kwargs: FakeScheduler()

    pixivpy3 = types.ModuleType("pixivpy3")
    pixivpy3.AppPixivAPI = object

    module_name = "task_two_subscription.utils.subscription"
    package = types.ModuleType("task_two_subscription")
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    utils_package = types.ModuleType("task_two_subscription.utils")
    utils_package.__path__ = [str(Path(__file__).resolve().parents[1] / "utils")]

    pixiv_utils = types.ModuleType("task_two_subscription.utils.pixiv_utils")
    pixiv_utils.filter_items = lambda items, tag_label, excluded_tags=None: (items, [])
    pixiv_utils.send_pixiv_image = None

    database = types.ModuleType("task_two_subscription.utils.database")
    database.get_all_subscriptions = lambda: []
    database.update_last_notified_id = lambda *args, **kwargs: None

    tag = types.ModuleType("task_two_subscription.utils.tag")
    tag.build_detail_message = lambda illust, is_novel=False: ""

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["apscheduler"] = apscheduler
    sys.modules["apscheduler.schedulers"] = apscheduler_schedulers
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio
    sys.modules["pixivpy3"] = pixivpy3
    sys.modules["task_two_subscription"] = package
    sys.modules["task_two_subscription.utils"] = utils_package
    sys.modules["task_two_subscription.utils.pixiv_utils"] = pixiv_utils
    sys.modules["task_two_subscription.utils.database"] = database
    sys.modules["task_two_subscription.utils.tag"] = tag
    sys.modules.pop(module_name, None)

    module_path = Path(__file__).resolve().parents[1] / "utils" / "subscription.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    subscription = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = subscription
    spec.loader.exec_module(subscription)
    return subscription


class SubscriptionDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_artist_updates_pass_automatic_exclusions_to_filtering(self):
        subscription = load_subscription_module()

        captured_excluded_tags = None
        updated_illust_ids = []

        def fake_filter_items(items, tag_label, excluded_tags=None):
            nonlocal captured_excluded_tags
            captured_excluded_tags = excluded_tags
            return [], []

        original_filter_items = subscription.filter_items
        original_update_last_notified_id = subscription.update_last_notified_id
        subscription.filter_items = fake_filter_items
        subscription.update_last_notified_id = (
            lambda chat_id, sub_type, target_id, illust_id: updated_illust_ids.append(
                illust_id
            )
        )

        try:
            service = object.__new__(subscription.SubscriptionService)
            service.client = FakeClient()
            service.pixiv_config = types.SimpleNamespace(
                automatic_push_excluded_tags=["ntr", "悪堕ち"]
            )
            sub = types.SimpleNamespace(
                target_id="123",
                target_name="artist",
                last_notified_illust_id=1,
                chat_id="456",
                sub_type="artist",
            )

            await service.check_artist_updates(sub)
        finally:
            subscription.filter_items = original_filter_items
            subscription.update_last_notified_id = original_update_last_notified_id

        self.assertEqual(captured_excluded_tags, ["ntr", "悪堕ち"])
        self.assertEqual(updated_illust_ids, [2])


if __name__ == "__main__":
    unittest.main()
