import unittest
from types import SimpleNamespace

from utils.random_group_config import resolve_random_search_runtime_config


class RandomSearchGroupConfigTest(unittest.TestCase):
    def make_base(self):
        return SimpleNamespace(
            return_count=3,
            min_likes=500,
            random_search_min_interval=50,
            random_search_max_interval=240,
        )

    def test_uses_global_values_without_group_override(self):
        resolved = resolve_random_search_runtime_config(self.make_base(), None)

        self.assertEqual(resolved.return_count, 3)
        self.assertIsNone(resolved.min_likes)
        self.assertEqual(resolved.min_interval_minutes, 50)
        self.assertEqual(resolved.max_interval_minutes, 240)

    def test_group_override_replaces_count_likes_and_interval(self):
        group = SimpleNamespace(
            return_count=2,
            min_likes=1000,
            min_interval_minutes=60,
            max_interval_minutes=300,
        )

        resolved = resolve_random_search_runtime_config(self.make_base(), group)

        self.assertEqual(resolved.return_count, 2)
        self.assertEqual(resolved.min_likes, 1000)
        self.assertEqual(resolved.min_interval_minutes, 60)
        self.assertEqual(resolved.max_interval_minutes, 300)

    def test_zero_min_likes_is_explicit_override(self):
        group = SimpleNamespace(
            return_count=None,
            min_likes=0,
            min_interval_minutes=None,
            max_interval_minutes=None,
        )

        resolved = resolve_random_search_runtime_config(self.make_base(), group)

        self.assertEqual(resolved.return_count, 3)
        self.assertEqual(resolved.min_likes, 0)
        self.assertEqual(resolved.min_interval_minutes, 50)
        self.assertEqual(resolved.max_interval_minutes, 240)

    def test_invalid_interval_falls_back_to_valid_range(self):
        group = SimpleNamespace(
            return_count=2,
            min_likes=1000,
            min_interval_minutes=300,
            max_interval_minutes=60,
        )

        resolved = resolve_random_search_runtime_config(self.make_base(), group)

        self.assertEqual(resolved.min_interval_minutes, 300)
        self.assertEqual(resolved.max_interval_minutes, 300)


if __name__ == "__main__":
    unittest.main()
