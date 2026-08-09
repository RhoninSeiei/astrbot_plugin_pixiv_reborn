import asyncio
import tempfile
import unittest

from tests.test_random_push_retry import install_import_stubs, remove_import_stubs


class RandomSearchQueueConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        remove_import_stubs()

    def _make_service(self, random_search, max_concurrent_jobs=2):
        service = object.__new__(random_search.RandomSearchService)
        service.task_queue = asyncio.Queue()
        service.is_queue_processor_running = True
        service._is_running = True
        service.execution_locks = {}
        service.group_locks = {}
        service._active_queue_tasks = set()
        service.global_execution_semaphore = asyncio.Semaphore(max_concurrent_jobs)
        service._schedule_next_run_from_now = lambda chat_id, now, reason: now
        service._claim_execution = lambda chat_id: f"claim-{chat_id}"
        return service

    async def test_queue_processor_runs_different_groups_concurrently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            started = []
            both_started = asyncio.Event()

            async def execute_search_for_group(chat_id):
                started.append(chat_id)
                if len(started) == 2:
                    both_started.set()
                await asyncio.sleep(0.05)
                return 1

            service.execute_search_for_group = execute_search_for_group

            processor = asyncio.create_task(service._task_queue_processor())
            try:
                await service.task_queue.put(("group-a", "claim-a"))
                await service.task_queue.put(("group-b", "claim-b"))

                await asyncio.wait_for(both_started.wait(), timeout=1)
                self.assertEqual(set(started), {"group-a", "group-b"})
                await asyncio.wait_for(service.task_queue.join(), timeout=1)
            finally:
                processor.cancel()
                await processor

    async def test_queue_processor_skips_duplicate_group_while_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            started = []
            first_started = asyncio.Event()
            allow_finish = asyncio.Event()
            released = []
            original_release = random_search.release_random_search_execution
            random_search.release_random_search_execution = (
                lambda chat_id, token: released.append((chat_id, token))
            )

            async def execute_search_for_group(chat_id):
                started.append(chat_id)
                first_started.set()
                await allow_finish.wait()
                return 1

            service.execute_search_for_group = execute_search_for_group

            processor = asyncio.create_task(service._task_queue_processor())
            try:
                await service.task_queue.put(("group-a", "claim-a"))
                await service.task_queue.put(("group-a", "claim-b"))

                await asyncio.wait_for(first_started.wait(), timeout=1)
                await asyncio.sleep(0.05)
                self.assertEqual(started, ["group-a"])
                self.assertIn(("group-a", "claim-b"), released)

                allow_finish.set()
                await asyncio.wait_for(service.task_queue.join(), timeout=1)
                self.assertIn(("group-a", "claim-a"), released)
            finally:
                random_search.release_random_search_execution = original_release
                processor.cancel()
                await processor

    async def test_queue_item_schedules_next_run_after_executor_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_import_stubs(temp_dir)
            from utils import random_search

            service = self._make_service(random_search)
            scheduled = []
            released = []
            original_release = random_search.release_random_search_execution
            random_search.release_random_search_execution = (
                lambda chat_id, token: released.append((chat_id, token))
            )
            service._schedule_next_run_from_now = (
                lambda chat_id, now, reason: scheduled.append((chat_id, reason))
                or now
            )

            async def execute_search_for_group(chat_id):
                raise RuntimeError("pixiv failed")

            service.execute_search_for_group = execute_search_for_group

            try:
                await service.task_queue.put(("group-a", "claim-a"))
                await service.task_queue.get()
                await service._process_queue_item("group-a", "claim-a")
            finally:
                random_search.release_random_search_execution = original_release

            self.assertEqual(len(scheduled), 1)
            self.assertEqual(scheduled[0][0], "group-a")
            self.assertEqual(released, [("group-a", "claim-a")])


if __name__ == "__main__":
    unittest.main()
