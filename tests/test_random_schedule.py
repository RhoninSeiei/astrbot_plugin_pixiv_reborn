import unittest
from datetime import datetime

from utils.random_schedule import (
    is_in_quiet_hours,
    normalize_schedule_time,
    parse_time_of_day,
)


class RandomScheduleQuietHoursTest(unittest.TestCase):
    def test_parse_time_of_day_accepts_hour_minute(self):
        self.assertEqual(parse_time_of_day("08:30").hour, 8)
        self.assertEqual(parse_time_of_day("08:30").minute, 30)

    def test_quiet_hours_inside_same_day_window_moves_to_end(self):
        now = datetime(2026, 5, 11, 12, 15)

        self.assertTrue(is_in_quiet_hours(now, "12:00", "13:00"))
        self.assertEqual(
            normalize_schedule_time(now, "12:00", "13:00"),
            datetime(2026, 5, 11, 13, 0),
        )

    def test_quiet_hours_outside_same_day_window_keeps_time(self):
        now = datetime(2026, 5, 11, 14, 15)

        self.assertFalse(is_in_quiet_hours(now, "12:00", "13:00"))
        self.assertEqual(normalize_schedule_time(now, "12:00", "13:00"), now)

    def test_quiet_hours_crossing_midnight_moves_late_night_to_next_morning(self):
        now = datetime(2026, 5, 11, 23, 30)

        self.assertTrue(is_in_quiet_hours(now, "22:00", "08:00"))
        self.assertEqual(
            normalize_schedule_time(now, "22:00", "08:00"),
            datetime(2026, 5, 12, 8, 0),
        )

    def test_quiet_hours_crossing_midnight_moves_early_morning_to_same_morning(self):
        now = datetime(2026, 5, 11, 7, 30)

        self.assertTrue(is_in_quiet_hours(now, "22:00", "08:00"))
        self.assertEqual(
            normalize_schedule_time(now, "22:00", "08:00"),
            datetime(2026, 5, 11, 8, 0),
        )

    def test_disabled_or_equal_start_end_keeps_time(self):
        now = datetime(2026, 5, 11, 23, 30)

        self.assertEqual(
            normalize_schedule_time(now, "22:00", "08:00", enabled=False),
            now,
        )
        self.assertEqual(normalize_schedule_time(now, "08:00", "08:00"), now)


if __name__ == "__main__":
    unittest.main()
