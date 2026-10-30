import importlib.util
import sys
import types
import unittest
import urllib.parse
from unittest.mock import patch
from pathlib import Path


try:
    import requests  # noqa: F401
except ImportError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = lambda: object()
    sys.modules["requests"] = requests_stub

BOT_PATH = Path(__file__).resolve().parents[1] / "etisalat mix.py"
SPEC = importlib.util.spec_from_file_location("etisalat_mix_test", BOT_PATH)
bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bot)


P270 = "Hekaya_XRP_MIX_Parent_7"
P180 = "Hekaya_XRP_MIX_Parent_6"
CHILD_PRODUCT = "Hekaya_XRP_MIX_Child_1"
CHILD_DIAL = "01000000002"
OWNER_DIAL = "01000000001"


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if self.error:
            raise self.error
        return self.response


class FakeEtisalat:
    def __init__(self, role, events, plan=P270, balance=0, inquiry_values=None):
        self.role = role
        self.events = events
        self.current_dial = OWNER_DIAL if role == "owner" else CHILD_DIAL
        self.plan = plan
        self.balance = balance
        self.inquiry_values = {key: list(values) for key, values in (inquiry_values or {}).items()}
        self.peer = None

    def get_status_and_child(self, timeout=10):
        linked = CHILD_DIAL if self.role == "owner" else None
        return self.plan, self.balance, linked

    def get_transferable_mix(self, product_id):
        self.events.append(("inquiry", self.role, product_id))
        values = self.inquiry_values.get(product_id, [0])
        return values.pop(0) if len(values) > 1 else values[0]

    def transfer_mix(self, receiver, amount, product_name):
        self.events.append(("transfer", self.role, receiver, amount, product_name))
        if self.role == "owner":
            self.balance = max(0, self.balance - amount)
        elif self.peer is not None:
            self.peer.balance += amount
        return True

    def change_plan(self, plan, plan_id=""):
        self.events.append(("change", plan, plan_id))
        self.plan = plan
        return True


class LegacyCycleTests(unittest.TestCase):
    def test_transfer_inquiry_reads_assigned_sender_amount(self):
        api = bot.EtisalatAPI()
        api.current_dial = OWNER_DIAL
        api.session = FakeSession(FakeResponse(
            text=("<response><status>true</status><assigned><dial>01000000001</dial>"
                  "<value>321.9</value></assigned></response>")
        ))

        amount = api.get_transferable_mix(P270)

        self.assertEqual(amount, 321)
        method, url, kwargs = api.session.calls[0]
        self.assertEqual(method, "get")
        self.assertIn("/Saytar/rest/emerald/transferInquiry?req=", url)
        self.assertIn("transferInquiryRequest", urllib.parse.unquote(url))
        self.assertEqual(kwargs["timeout"], (5, 60))
        self.assertNotIn("verify", kwargs)  # requests keeps TLS verification enabled

    def test_transfer_unknown_responses_are_not_treated_as_rejections(self):
        api = bot.EtisalatAPI()
        api.current_dial = OWNER_DIAL
        api.session = FakeSession(FakeResponse(text="<response><status>pending</status></response>"))
        self.assertIsNone(api.transfer_mix(CHILD_DIAL, 10, P270))

        api.session = FakeSession(FakeResponse(text="<response><status>false</status></response>"))
        self.assertFalse(api.transfer_mix(CHILD_DIAL, 10, P270))

    def test_cycle_follows_old_six_steps_and_omits_minion_migration(self):
        events = []
        owner = FakeEtisalat(
            "owner", events, balance=100,
            inquiry_values={P270: [100], P180: [200]},
        )
        child = FakeEtisalat("child", events, inquiry_values={CHILD_PRODUCT: [300, 400]})
        child.peer = owner
        loop = bot.AutoLoop(owner, child, target_mix=400, retry_settings_provider=lambda: {
            "plan_retry_seconds": 60,
            "plan_retry_limit": 0,
            "transfer_retry_seconds": 1,
            "transfer_retry_limit": 3,
            "migration_settle_seconds": 0,
        })
        loop.initial_status = (P270, 100, CHILD_DIAL)
        loop._sleep = lambda seconds: True

        loop._run()

        meaningful = [event for event in events if event[0] in {"inquiry", "transfer", "change"}]
        self.assertEqual(
            meaningful,
            [
                ("inquiry", "owner", P270),
                ("transfer", "owner", CHILD_DIAL, 100, P270),
                ("change", P180, "951"),
                ("inquiry", "child", CHILD_PRODUCT),
                ("transfer", "child", OWNER_DIAL, 300, CHILD_PRODUCT),
                ("inquiry", "owner", P180),
                ("transfer", "owner", CHILD_DIAL, 200, P180),
                ("change", P270, ""),
                ("inquiry", "child", CHILD_PRODUCT),
                ("transfer", "child", OWNER_DIAL, 400, CHILD_PRODUCT),
            ],
        )
        self.assertEqual(loop.outcome, "success")
        self.assertGreaterEqual(owner.balance, 400)
        self.assertFalse(any("MINION" in str(event) for event in events))

    def test_unexpected_start_plan_fails_closed_without_changes_or_transfers(self):
        events = []
        owner_api = FakeEtisalat("owner", events, plan=P180, balance=100)
        child_api = FakeEtisalat("child", events)
        loop = bot.AutoLoop(owner_api, child_api, target_mix=100)
        loop.initial_status = (P180, 100, CHILD_DIAL)

        loop._run()

        self.assertEqual(events, [])
        self.assertEqual(loop.outcome, "failed")

    def test_plan_read_timeout_stops_cycle_without_retrying_or_continuing(self):
        events = []
        owner_api = FakeEtisalat("owner", events, balance=100, inquiry_values={P270: [100]})
        child_api = FakeEtisalat("child", events)

        def unclear_change(plan, plan_id=""):
            events.append(("change", plan, plan_id))
            return False

        owner_api.change_plan = unclear_change
        loop = bot.AutoLoop(owner_api, child_api, target_mix=1000, retry_settings_provider=lambda: {
            "plan_retry_seconds": 60,
            "plan_retry_limit": 3,
            "transfer_retry_seconds": 1,
            "transfer_retry_limit": 1,
            "migration_settle_seconds": 0,
        })
        loop.initial_status = (P270, 100, CHILD_DIAL)
        loop._sleep = lambda seconds: True
        original_read = loop._read_status

        def unreadable_plan_confirmation(api, purpose):
            if purpose.startswith("تأكيد النظام"):
                return None
            return original_read(api, purpose)

        loop._read_status = unreadable_plan_confirmation
        loop._run()

        self.assertEqual([event for event in events if event[0] == "change"], [("change", P180, "951")])
        self.assertFalse(any(event[0] == "inquiry" and event[1] == "child" for event in events))

    def test_plan_retry_requires_interval_and_readable_old_plan(self):
        events = []
        owner_api = FakeEtisalat("owner", events, plan=P270)
        loop = bot.AutoLoop(owner_api, FakeEtisalat("child", events), target_mix=100, retry_settings_provider=lambda: {
            "plan_retry_seconds": 60,
            "plan_retry_limit": 1,
        })
        loop._sleep = lambda seconds: True

        with patch.object(bot.time, "monotonic", side_effect=[0, 61, 61.1, 62]):
            confirmed = loop._wait_for_plan(P180, previous_plan_id=P270, target_pid="951")

        self.assertTrue(confirmed)
        self.assertEqual([event for event in events if event[0] == "change"], [("change", P180, "951")])

    def test_ambiguous_transfer_retries_only_after_two_unchanged_inquiries(self):
        class Sender:
            def __init__(self):
                self.reads = [100, 100, 100]
                self.sent = []

            def get_transferable_mix(self, product):
                return self.reads.pop(0)

            def transfer_mix(self, receiver, amount, product):
                self.sent.append(amount)
                return None if len(self.sent) == 1 else True

        sender = Sender()
        loop = bot.AutoLoop(object(), object(), target_mix=1, retry_settings_provider=lambda: {
            "transfer_retry_seconds": 1,
            "transfer_retry_limit": 1,
        })
        loop._sleep = lambda seconds: True

        self.assertTrue(loop._transfer_available_product(sender, CHILD_DIAL, "P", "P", "test"))
        self.assertEqual(sender.sent, [100, 100])
        self.assertEqual(sender.reads, [])

    def test_changed_inquiry_amount_is_used_instead_of_replaying_old_amount(self):
        class Sender:
            def __init__(self):
                self.reads = [100, 50]
                self.sent = []

            def get_transferable_mix(self, product):
                return self.reads.pop(0)

            def transfer_mix(self, receiver, amount, product):
                self.sent.append(amount)
                return None if len(self.sent) == 1 else True

        sender = Sender()
        loop = bot.AutoLoop(object(), object(), target_mix=1, retry_settings_provider=lambda: {
            "transfer_retry_seconds": 1,
            "transfer_retry_limit": 1,
        })
        loop._sleep = lambda seconds: True

        self.assertTrue(loop._transfer_available_product(sender, CHILD_DIAL, "P", "P", "test"))
        self.assertEqual(sender.sent, [100, 50])


if __name__ == "__main__":
    unittest.main()
