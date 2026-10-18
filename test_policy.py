import unittest

from policy import heartbeat_age_minutes, should_probe


class PolicyTests(unittest.TestCase):
    def test_timezone_conversion_and_boundary(self):
        self.assertEqual(
            heartbeat_age_minutes("2026-09-01T08:15:00+08:00", "2026-09-01T00:00:00Z"), 15
        )
        self.assertTrue(should_probe("2026-09-01T08:15:00+08:00", "2026-09-01T00:00:00Z"))
        self.assertFalse(should_probe("2026-09-01T08:14:59+08:00", "2026-09-01T00:00:00Z"))

    def test_reject_future_heartbeat(self):
        with self.assertRaises(ValueError):
            should_probe("2026-09-01T08:00:00+08:00", "2026-09-01T00:01:00Z")


if __name__ == "__main__":
    unittest.main()
