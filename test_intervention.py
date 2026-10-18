"""Synthetic fixtures test behavior only; they are not evidence for data claims."""

import unittest

from intervention import Command, InterventionService


class FakeGateway:
    def __init__(self, probes=(), sends=(), states=()):
        self.probes = iter(probes)
        self.sends = iter(sends)
        self.states = iter(states)
        self.sent = []
        self.notices = []
        self.escalations = []

    def probe(self, device_id):
        return next(self.probes)

    def dispatch(self, command):
        self.sent.append(command.command_id)
        return next(self.sends)

    def state_matches(self, command):
        return next(self.states)

    def notify(self, command, message):
        self.notices.append(message)

    def escalate(self, command, reason):
        self.escalations.append(reason)


def command(safe_to_retry=True):
    return Command("c1", "d1", "turn_on", "on", "2026-09-01T08:30:00+08:00",
                   "2026-09-01T08:00:00+08:00", "2026-09-01T09:00:00+08:00",
                   safe_to_retry)


class InterventionTests(unittest.TestCase):
    def test_offline_queue_then_recovery(self):
        gateway = FakeGateway(probes=[False, True], sends=[True])
        service = InterventionService(gateway)
        self.assertEqual(service.submit(command()), "queued")
        self.assertEqual(gateway.sent, [])
        self.assertEqual(service.on_reconnected("d1", "2026-09-01T08:40:00+08:00"),
                         ["completed"])
        self.assertEqual(gateway.sent, ["c1"])
        self.assertEqual(service.submit(command()), "completed")

    def test_no_retry_when_result_unknown(self):
        gateway = FakeGateway(probes=[True], sends=[False], states=[None])
        service = InterventionService(gateway)
        self.assertEqual(service.submit(command()), "unconfirmed")
        self.assertEqual(gateway.sent, ["c1"])
        self.assertEqual(len(gateway.escalations), 1)
        self.assertEqual(service.submit(command()), "unconfirmed")
        self.assertEqual(gateway.sent, ["c1"])

    def test_one_safe_retry_after_confirmed_not_applied(self):
        gateway = FakeGateway(probes=[True], sends=[False, True], states=[False])
        service = InterventionService(gateway)
        self.assertEqual(service.submit(command()), "completed")
        self.assertEqual(gateway.sent, ["c1", "c1"])

    def test_unsafe_command_is_not_retried(self):
        gateway = FakeGateway(probes=[True], sends=[False], states=[False])
        service = InterventionService(gateway)
        self.assertEqual(service.submit(command(safe_to_retry=False)), "unconfirmed")
        self.assertEqual(gateway.sent, ["c1"])

    def test_queued_command_expires_without_send(self):
        gateway = FakeGateway(probes=[False])
        service = InterventionService(gateway)
        self.assertEqual(service.submit(command()), "queued")
        self.assertEqual(service.on_reconnected("d1", "2026-09-01T09:00:00+08:00"),
                         ["expired"])
        self.assertEqual(gateway.sent, [])
        self.assertEqual(len(gateway.escalations), 1)


if __name__ == "__main__":
    unittest.main()
