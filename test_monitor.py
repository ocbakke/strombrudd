import unittest
from datetime import UTC, datetime, timedelta

from outage_monitor import Outage, detect_alerts


def outage(
    key: str,
    customers: int,
    *,
    municipality: str = "Sarpsborg",
    expected_end: datetime | None = None,
) -> Outage:
    return Outage(
        key=key,
        source="Elvia",
        source_id=key.split(":", 1)[-1],
        municipality=municipality,
        place="Sarpsborg",
        customers=customers,
        kind="unplanned",
        started_at=datetime(2026, 8, 20, 12, tzinfo=UTC).isoformat(),
        expected_end=expected_end.isoformat() if expected_end else None,
        description="",
        source_url="https://example.com",
    )


class DetectAlertsTest(unittest.TestCase):
    def test_small_new_outage_does_not_alert(self):
        current = {"elvia:1": outage("elvia:1", 49)}
        self.assertEqual(detect_alerts(current, {}, {"Elvia"}), [])

    def test_new_outage_at_threshold_alerts(self):
        current = {"elvia:1": outage("elvia:1", 50)}
        alerts = detect_alerts(current, {}, {"Elvia"})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].category, "new")

    def test_doubled_customer_count_alerts(self):
        previous = {"elvia:1": outage("elvia:1", 60)}
        current = {"elvia:1": outage("elvia:1", 120)}
        alerts = detect_alerts(current, previous, {"Elvia"})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].category, "update")

    def test_one_hour_delay_alerts(self):
        first_end = datetime(2026, 8, 20, 14, tzinfo=UTC)
        previous = {"elvia:1": outage("elvia:1", 80, expected_end=first_end)}
        current = {
            "elvia:1": outage(
                "elvia:1", 80, expected_end=first_end + timedelta(hours=1)
            )
        }
        alerts = detect_alerts(current, previous, {"Elvia"})
        self.assertEqual(len(alerts), 1)

    def test_multiple_small_outages_alert_in_combination(self):
        current = {
            "elvia:1": outage("elvia:1", 40),
            "elvia:2": outage("elvia:2", 35),
            "elvia:3": outage("elvia:3", 30),
        }
        alerts = detect_alerts(current, {}, {"Elvia"})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].category, "combined")

    def test_large_outage_disappearing_alerts_as_resolved(self):
        previous = {"elvia:1": outage("elvia:1", 300)}
        alerts = detect_alerts({}, previous, {"Elvia"})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].category, "resolved")

    def test_failed_source_does_not_create_resolved_alert(self):
        previous = {"elvia:1": outage("elvia:1", 300)}
        alerts = detect_alerts({}, previous, {"Norgesnett"})
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
