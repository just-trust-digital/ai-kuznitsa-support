import tempfile
import unittest
from pathlib import Path

from backend.core import Store


class SalesChatTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "stand.db")
        self.scope = "jury-sales"

    def act(self, role, action, **payload):
        return self.store.act(role, action, payload, self.scope)

    def message(self, text, **fields):
        return self.act("buyer", "message", text=text, mode="rules", **fields)["dialog"]

    def test_knowledge_from_regular_reply_is_used_for_another_buyer(self):
        first = self.message("Как изменить язык уведомлений?")
        assigned = self.act("buyer", "handoff", dialog_id=first["id"])["dialog"]
        answer = "Откройте настройки профиля и выберите язык уведомлений."
        self.act(assigned["assignment"], "reply", dialog_id=first["id"], text=answer)
        self.act("buyer", "buyer_select", buyer_id="buyer2")
        second = self.message("Как поменять язык уведомлений?")
        self.assertEqual(second["messages"][-1]["text"], answer)
        self.assertIsNone(second["assignment"])
        self.act(assigned["assignment"], "reply", dialog_id=first["id"], text="Откройте договор компании 1234.")
        self.assertEqual(self.message("Как сменить язык уведомлений?")["messages"][-1]["text"], answer)
        self.assertNotEqual(self.message("Как отключить уведомления?")["messages"][-1]["text"], answer)

    def test_qualifies_only_missing_fields_in_assigned_chat(self):
        first = self.message("Нужна программа на 12 рабочих мест для Windows")
        dialog = self.act("buyer", "price_request", offer_id="demo-necessity", dialog_id=first["id"])["dialog"]
        self.assertEqual(dialog["assignment"], "manager1")
        self.assertIn("Картой", dialog["messages"][-1]["choices"])
        last = self.message("Картой", dialog_id=first["id"])
        self.assertEqual(last["decision"]["next_action"], "await_manager")
        self.assertNotIn("?", last["messages"][-1]["text"])

    def test_email_after_hours(self):
        self.store.business_hours = lambda: False
        dialog = self.act("buyer", "handoff")["dialog"]
        self.assertTrue(dialog["offer_email"])
        self.assertIsNone(dialog["assignment"])
        sent = self.message("Отправить на почту", dialog_id=dialog["id"])
        self.assertEqual(sent["messages"][-1]["text"], "Отправлено на почту.")
        self.assertFalse(sent["offer_email"])
