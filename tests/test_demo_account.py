import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.core import Store
from backend.customer import request_customer


class DemoAccountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = Mock()
        self.hybrid = Mock()
        self.store = Store(Path(temporary.name) / "account.db", engine=self.engine,
                           hybrid_engine=self.hybrid)
        self.scope = "jury-profile-test"

    def ask(self, buyer, text, mode, dialog_id=None):
        context = request_customer.set(buyer)
        try:
            result = self.store.act("buyer", "message", {"text": text, "mode": mode,
                                                         **({"dialog_id": dialog_id} if dialog_id else {})}, self.scope)
            state = self.store.snapshot("buyer", self.scope)
        finally:
            request_customer.reset(context)
        self.assertFalse(self.engine.mock_calls, "Profile lookup must stay local")
        self.assertFalse(self.hybrid.mock_calls, "Profile lookup must stay local")
        return result["dialog"], state

    def test_active_key_matches_customer_inventory_in_each_mode(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                dialog, state = self.ask("buyer1", "Покупал Dr.Web, ключ найти не могу. [номер]", mode)
                row = next(row for row in state["licenses"] if row["name"] == "Dr.Web")
                self.assertEqual(state["buyer_id"], "buyer1")
                self.assertEqual(row["status"], "active")
                self.assertIn(row["key"], dialog["messages"][-1]["text"])
                self.assertEqual(dialog["decision"]["intent"], "license_lookup")
                self.assertEqual(dialog["decision"]["next_action"], "lookup")
                self.assertTrue(dialog["messages"][-1]["synthetic"])

    def test_expired_purchase_does_not_return_key(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                dialog, state = self.ask("buyer2", "Покупал Dr.Web, ключ найти не могу. [номер]", mode)
                row = next(row for row in state["licenses"] if row["name"] == "Dr.Web")
                self.assertEqual(row["status"], "expired")
                self.assertNotIn("key", row)
                self.assertIn("истёк", dialog["messages"][-1]["text"])
                self.assertNotIn("DEMO-KEY-", dialog["messages"][-1]["text"])
                self.assertEqual(dialog["decision"]["next_action"], "lookup")
                self.assertTrue(dialog["messages"][-1]["synthetic"])

    def test_product_reply_continues_license_clarification(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                first, _ = self.ask("buyer1", "Покупал программу, ключ найти не могу", mode)
                self.assertEqual(first["decision"]["intent"], "license_lookup")
                self.assertEqual(first["decision"]["next_action"], "clarify")
                dialog, state = self.ask("buyer1", "Dr.Web", mode, first["id"])
                row = next(row for row in state["licenses"] if row["name"] == "Dr.Web")
                self.assertEqual(dialog["decision"]["next_action"], "lookup")
                self.assertIn(row["key"], dialog["messages"][-1]["text"])

    def test_owned_license_number_finds_its_exact_key(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                _, state = self.ask("buyer1", "Покупал Dr.Web, ключ найти не могу. [номер]", mode)
                row = next(row for row in state["licenses"] if row["name"] == "Microsoft Office")
                dialog, _ = self.ask("buyer1", "Покажите мой ключ, номер лицензии " + row["id"], mode)
                self.assertEqual(dialog["decision"]["next_action"], "lookup")
                self.assertIn(row["key"], dialog["messages"][-1]["text"])

    def test_license_number_request_returns_number(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                dialog, state = self.ask("buyer1", "Покупал Microsoft Office, номер лицензии найти не могу", mode)
                row = next(row for row in state["licenses"] if row["name"] == "Microsoft Office")
                self.assertEqual(dialog["decision"]["next_action"], "lookup")
                self.assertIn(row["id"], dialog["messages"][-1]["text"])
                self.assertNotIn(row["key"], dialog["messages"][-1]["text"])

    def test_missing_personal_purchase_goes_to_manager(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                dialog, state = self.ask("personal", "Покупал Dr.Web, ключ найти не могу. [номер]", mode)
                self.assertFalse(any(row["name"] == "Dr.Web" for row in state["licenses"]))
                self.assertEqual(dialog["decision"]["next_action"], "handoff")
                self.assertIn(dialog["status"], ("assigned", "queued"))
                self.assertNotIn("DEMO-KEY-", dialog["messages"][-1]["text"])
                self.assertTrue(dialog["messages"][-1]["synthetic"])

    def test_missing_purchase_in_queue_retains_synthetic_marker(self):
        self.store.act("manager1", "availability", {"available": False}, self.scope)
        dialog, _ = self.ask("personal", "Покупал Dr.Web, ключ найти не могу. [номер]", "rules")
        self.assertEqual(dialog["status"], "queued")
        self.assertEqual(dialog["decision"]["next_action"], "handoff")
        self.assertIn("Обращение в очереди", dialog["messages"][-1]["text"])
        self.assertTrue(dialog["messages"][-1]["synthetic"])

    def test_guest_cannot_obtain_profile_licenses(self):
        for mode in ("rules", "local", "hybrid"):
            with self.subTest(mode=mode):
                dialog, state = self.ask("visitor-profile-test", "Покупал Dr.Web, ключ найти не могу. [номер]", mode)
                self.assertEqual(state["licenses"], [])
                self.assertEqual(dialog["decision"]["next_action"], "login")
                self.assertNotIn("DEMO-KEY-", json.dumps(dialog))

    def test_foreign_unknown_and_mixed_references_never_fall_back_to_active_key(self):
        _, owner = self.ask("buyer1", "Покупал Dr.Web, ключ найти не могу. [номер]", "rules")
        foreign = next(row for row in owner["licenses"] if row["name"] == "Microsoft Office")
        for mode in ("rules", "local", "hybrid"):
            for reference in (foreign["id"], foreign["key"], "DEMO-LIC-NOTFOUND", "номер лицензии 123456",
                              foreign["id"] + " DEMO-LIC-NOTFOUND"):
                with self.subTest(mode=mode, reference=reference):
                    dialog, _ = self.ask("buyer2", "Покажите мой ключ: " + reference, mode)
                    self.assertEqual(dialog["decision"]["next_action"], "handoff")
                    self.assertNotIn("DEMO-KEY-", dialog["messages"][-1]["text"])

    def test_company_contract_and_edi_follow_selected_profile(self):
        for mode in ("rules", "local", "hybrid"):
            for buyer, contract, edi in (("buyer1", "есть действующий договор", "ЭДО подключён"),
                                          ("buyer2", "нет действующего договора", "ЭДО не подключён")):
                with self.subTest(mode=mode, buyer=buyer):
                    dialog, _ = self.ask(buyer, "Мы из ООО «Учебный заказчик». Есть ли у нас договор? Есть ли у нас ЭДО?", mode)
                    answer = dialog["messages"][-1]
                    self.assertIn(contract, answer["text"])
                    self.assertIn(edi, answer["text"])
                    self.assertTrue(answer["synthetic"])
                    self.assertEqual(dialog["decision"]["next_action"], "lookup")


if __name__ == "__main__":
    unittest.main()
