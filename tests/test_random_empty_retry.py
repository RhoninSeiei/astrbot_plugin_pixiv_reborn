import unittest

from utils.random_empty_retry import (
    build_retry_source_sequence,
    enforce_random_push_delivery_policy,
    is_random_push_image_failure_notice,
    resolve_retry_depth,
)


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeMessageChain:
    def __init__(self, chain):
        self.chain = chain


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

    def test_random_push_delivery_policy_forces_direct_silent_messages(self):
        config_kwargs = {
            "show_filter_result": True,
            "single_response_mode": True,
            "forward_threshold": True,
            "return_count": 3,
        }

        normalized = enforce_random_push_delivery_policy(config_kwargs)

        self.assertFalse(normalized["show_filter_result"])
        self.assertFalse(normalized["single_response_mode"])
        self.assertFalse(normalized["forward_threshold"])
        self.assertEqual(normalized["return_count"], 3)
        self.assertTrue(config_kwargs["single_response_mode"])

    def test_image_failure_notice_is_detected_from_message_chain(self):
        message = FakeMessageChain(
            [FakePlain("图片下载失败，仅发送信息：\n标题: 時雨")]
        )

        self.assertTrue(is_random_push_image_failure_notice(message))

    def test_normal_detail_message_is_not_detected_as_failure_notice(self):
        message = FakeMessageChain([FakePlain("标题: 時雨\n链接: https://example.com")])

        self.assertFalse(is_random_push_image_failure_notice(message))


if __name__ == "__main__":
    unittest.main()
