import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta

from tests.test_random_push_retry import install_import_stubs, remove_import_stubs


class RandomSearchScheduleClaimTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        remove_import_stubs()

    async def test_scheduler_tick_claims_due_group_without_pre_scheduling_next_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = object.__new__(random_search.RandomSearchService)
            service.client = object()
            service._is_running = True
            service.task_queue = asyncio.Queue()
            service.execution_locks = {}
            service.group_locks = {}
            service.is_queue_processor_running = True
            service._queue_processor_task = asyncio.create_task(asyncio.sleep(60))

            schedule_calls = []
            service._normalize_schedule_time = lambda candidate: candidate
            service._claim_execution = (
                lambda chat_id, now=None: f"claim-{chat_id}"
            )
            service._schedule_next_run_from_now = (
                lambda chat_id, now, reason: schedule_calls.append(
                    (chat_id, now, reason)
                )
                or now
            )

            originals = {
                "get_all_random_search_groups": random_search.get_all_random_search_groups,
                "get_all_random_ranking_groups": random_search.get_all_random_ranking_groups,
                "get_schedule_time": random_search.get_schedule_time,
                "release_random_search_execution": random_search.release_random_search_execution,
            }
            released = []
            random_search.get_all_random_search_groups = lambda: ["group-a"]
            random_search.get_all_random_ranking_groups = lambda: []
            random_search.get_schedule_time = (
                lambda chat_id: datetime.now() - timedelta(minutes=1)
            )
            random_search.release_random_search_execution = (
                lambda chat_id, token: released.append((chat_id, token))
            )

            try:
                await service._scheduler_tick()
            finally:
                for name, value in originals.items():
                    setattr(random_search, name, value)
                service._queue_processor_task.cancel()
                try:
                    await service._queue_processor_task
                except asyncio.CancelledError:
                    pass

            queued = await asyncio.wait_for(service.task_queue.get(), timeout=1)
            service.task_queue.task_done()

            self.assertEqual(queued, ("group-a", "claim-group-a"))
            self.assertEqual(schedule_calls, [])
            self.assertEqual(released, [])


if __name__ == "__main__":
    unittest.main()
