import unittest

from fpga_feedback_age import FeedbackAgeClock, UNKNOWN


class Clock:
    def __init__(self, wall=1_700_000_000.0, monotonic=100.0):
        self.wall = wall
        self.monotonic = monotonic

    def wall_time(self):
        return self.wall

    def monotonic_ns(self):
        return int(self.monotonic * 1_000_000_000)


def snapshot(stamp):
    return {side: {part: stamp for part in ("joint", "gripper")}
            for side in ("left", "right")}


class FeedbackAgeTests(unittest.TestCase):
    def test_uses_oldest_of_both_arms_and_sensors(self):
        clock = Clock()
        age = FeedbackAgeClock(wall_time=clock.wall_time, monotonic_ns=clock.monotonic_ns, allow_wall_clock=True)
        values = snapshot(clock.wall - 0.020)
        values["right"]["gripper"] = clock.wall - 0.075
        self.assertGreaterEqual(age.age_us(values), 75_000)
        self.assertLessEqual(age.age_us(values), 75_001)

    def test_unavailable_or_implausible_timestamp_is_unknown(self):
        clock = Clock()
        age = FeedbackAgeClock(wall_time=clock.wall_time, monotonic_ns=clock.monotonic_ns, allow_wall_clock=True)
        self.assertEqual(age.age_us(None), UNKNOWN)
        self.assertEqual(age.age_us(snapshot(0)), UNKNOWN)
        self.assertEqual(age.age_us(snapshot(clock.wall + 61)), UNKNOWN)

    def test_wall_clock_step_invalidates_one_sample(self):
        clock = Clock()
        age = FeedbackAgeClock(wall_time=clock.wall_time, monotonic_ns=clock.monotonic_ns, allow_wall_clock=True)
        values = snapshot(clock.wall - .01)
        self.assertEqual(age.age_us(values), 10_000)
        clock.wall += 2
        self.assertEqual(age.age_us(snapshot(clock.wall - .01)), UNKNOWN)
        self.assertEqual(age.age_us(snapshot(clock.wall - .01)), 10_000)


if __name__ == "__main__":
    unittest.main()
