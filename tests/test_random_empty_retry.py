import unittest

from utils.random_empty_retry import (
    build_retry_source_sequence,
    resolve_retry_depth,
)


class RandomSearchEmptyRetryTest(unittest.TestCase):
    def test_retry_depth_expands_positive_depth_with_cap(self):
        self.assertEqual(resolve_retry_depth(3, 3), 6)
        self.assertEqual(resolve_retry_depth(9, 5), 10)
        self.assertEqual(resolve_retry_depth(20, 5), 20)

    def test_retry_depth_does_not_expand_unlimited_or_disabled_values(self):
        self.assertEqual(resolve_retry_depth(-1, 3), -1)
        self.assertEqual(resolve_retry_depth(0, 3), 0)
        self.assertEqual(resolve_retry_depth(3, 0), 3)

    def test_retry_source_sequence_keeps_selected_first_and_limits_extras(self):
        selected = ("tag", "イレイナ")
        options = [
            selected,
            ("tag", "ビカラ"),
            ("ranking", "day"),
            ("tag", "響"),
        ]

        sequence = build_retry_source_sequence(
            options,
            selected,
            retry_sources=2,
            shuffle_func=lambda items: items.reverse(),
        )

        self.assertEqual(
            sequence,
            [
                ("tag", "イレイナ"),
                ("tag", "響"),
                ("ranking", "day"),
            ],
        )

    def test_retry_source_sequence_treats_negative_count_as_no_extra_sources(self):
        selected = ("tag", "イレイナ")

        self.assertEqual(
            build_retry_source_sequence(
                [selected, ("tag", "ビカラ")],
                selected,
                retry_sources=-1,
            ),
            [selected],
        )


if __name__ == "__main__":
    unittest.main()
