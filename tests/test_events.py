import unittest

from events import EventLog, TransitionTracker, make_event


class TransitionTrackerTests(unittest.TestCase):
    def test_first_observation_is_a_transition_with_old_none(self):
        t = TransitionTracker()
        result = t.check("core_reachable", True)
        self.assertIsNotNone(result)
        self.assertIsNone(result["old"])

    def test_no_event_on_repeated_same_value(self):
        t = TransitionTracker()
        t.check("core_reachable", True)
        result = t.check("core_reachable", True)
        self.assertIsNone(result)

    def test_event_on_real_change(self):
        t = TransitionTracker()
        t.check("core_reachable", True)
        result = t.check("core_reachable", False)
        self.assertIsNotNone(result)
        self.assertEqual(result["old"], True)
        self.assertEqual(result["new"], False)

    def test_reset_forgets_state(self):
        t = TransitionTracker()
        t.check("x", True)
        t.reset("x")
        result = t.check("x", True)
        self.assertIsNone(result["old"])


class EventLogTests(unittest.TestCase):
    def test_bounded_size(self):
        log = EventLog()
        from events import MAX_EVENTS_IN_MEMORY

        for i in range(MAX_EVENTS_IN_MEMORY + 50):
            log.add(make_event("test", "info", f"event {i}"))
        self.assertLessEqual(len(log.recent(limit=MAX_EVENTS_IN_MEMORY + 100)), MAX_EVENTS_IN_MEMORY)

    def test_recent_most_recent_first(self):
        log = EventLog()
        log.add(make_event("a", "info", "first"))
        log.add(make_event("b", "info", "second"))
        recent = log.recent()
        self.assertEqual(recent[0]["type"], "b")

    def test_severity_filter(self):
        log = EventLog()
        log.add(make_event("a", "info", "info event"))
        log.add(make_event("b", "critical", "critical event"))
        filtered = log.recent(min_severity="critical")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["type"], "b")


if __name__ == "__main__":
    unittest.main()
