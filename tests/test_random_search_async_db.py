import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta

from tests.test_random_push_retry import install_import_stubs


class RandomSearchAsyncDatabaseTest(unittest.IsolatedAsyncioTestCase):
    def _make_service(self, random_search):
        service = object.__new__(random_search.RandomSearchService)
        service.client = object()
        service._is_running = True
        service.task_queue = asyncio.Queue()
        service.execution_locks = {}
        service.group_locks = {}
        service.is_queue_processor_running = True
        service._queue_processor_task = asyncio.create_task(asyncio.sleep(60))
        service._normalize_schedule_time = lambda candidate: candidate
        service._schedule_next_run_from_now = lambda chat_id, now, reason: now
        service._empty_retry_enabled = lambda: False
        return service

    async def test_execute_group_reads_sources_through_to_thread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            calls = []

            async def fake_to_thread(func, *args, **kwargs):
                calls.append(getattr(func, "__name__", repr(func)))
                return func(*args, **kwargs)

            originals = {
                "get_random_tags": random_search.get_random_tags,
                "get_random_rankings": random_search.get_random_rankings,
                "to_thread": random_search.asyncio.to_thread,
            }

            def get_random_tags(chat_id):
                return []

            def get_random_rankings(chat_id):
                return []

            random_search.get_random_tags = get_random_tags
            random_search.get_random_rankings = get_random_rankings
            random_search.asyncio.to_thread = fake_to_thread

            try:
                sent_count = await service.execute_search_for_group("group-a")
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.asyncio if name == "to_thread" else random_search,
                        name,
                        value,
                    )
                service._queue_processor_task.cancel()
                try:
                    await service._queue_processor_task
                except asyncio.CancelledError:
                    pass

            self.assertEqual(sent_count, 0)
            self.assertIn("get_random_tags", calls)
            self.assertIn("get_random_rankings", calls)

    async def test_scheduler_tick_reads_and_claims_through_to_thread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            calls = []

            def claim_execution(chat_id, now=None):
                return f"claim-{chat_id}"

            async def fake_to_thread(func, *args, **kwargs):
                calls.append(getattr(func, "__name__", repr(func)))
                return func(*args, **kwargs)

            service._claim_execution = claim_execution
            originals = {
                "get_all_random_search_groups": random_search.get_all_random_search_groups,
                "get_all_random_ranking_groups": random_search.get_all_random_ranking_groups,
                "get_schedule_time": random_search.get_schedule_time,
                "to_thread": random_search.asyncio.to_thread,
            }

            def get_all_random_search_groups():
                return ["group-a"]

            def get_all_random_ranking_groups():
                return []

            def get_schedule_time(chat_id):
                return datetime.now() - timedelta(minutes=1)

            random_search.get_all_random_search_groups = get_all_random_search_groups
            random_search.get_all_random_ranking_groups = get_all_random_ranking_groups
            random_search.get_schedule_time = get_schedule_time
            random_search.asyncio.to_thread = fake_to_thread

            try:
                await service._scheduler_tick()
            finally:
                for name, value in originals.items():
                    setattr(
                        random_search.asyncio if name == "to_thread" else random_search,
                        name,
                        value,
                    )
                service._queue_processor_task.cancel()
                try:
                    await service._queue_processor_task
                except asyncio.CancelledError:
                    pass

            queued = await asyncio.wait_for(service.task_queue.get(), timeout=1)
            service.task_queue.task_done()

            self.assertEqual(queued, ("group-a", "claim-group-a"))
            self.assertIn("get_all_random_search_groups", calls)
            self.assertIn("get_all_random_ranking_groups", calls)
            self.assertIn("get_schedule_time", calls)
            self.assertIn("claim_execution", calls)


if __name__ == "__main__":
    unittest.main()
