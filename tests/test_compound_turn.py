import tempfile
import unittest
from pathlib import Path
from backend.core import Store


class CompoundTurnTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "test.db")
        self.scope = "parts-check"

    def ask(self, text, **extra):
        return self.store.act("buyer", "message", dict(text=text, mode="rules", **extra), self.scope)["dialog"]

    def test_each_request_has_attributable_output(self):
        dialog = self.ask("Сколько стоит Dr.Web? Кто производитель? Где скачать счёт по заказу?")
        parts = dialog["decision"]["parts"]
        self.assertEqual(len(parts), 3)
        messages = {m["id"]: m for m in dialog["messages"]}
        for part in parts:
            self.assertTrue(part["replies"])
            self.assertTrue(all(messages[i]["role"] == "assistant" for i in part["replies"]))
        self.assertIn("demo-drweb", parts[1]["evidence"])
        self.assertEqual(parts[1]["subjects"][0]["vendor"], "Доктор Веб")
        self.assertTrue(any(s.endswith("/invoices") for s in parts[2]["sources"]))

    def test_property_evidence_and_handoff_keep_independent_help(self):
        dialog = self.ask("Какой срок действия лицензии Dr.Web? Какой срок обновлений? Где скачать счёт?")
        parts = dialog["decision"]["parts"]
        self.assertEqual(parts[0]["evidence"], ["demo-drweb", "Срок действия"])
        self.assertEqual(parts[1]["action"], "handoff")
        self.assertIn("Срок обновлений", parts[1]["reason"])
        self.assertTrue(any(s.endswith("/invoices") for s in parts[-1]["sources"]))
        self.assertEqual(dialog["decision"]["next_action"], "handoff")

    def test_conditional_purchase_needs_fresh_confirmation(self):
        cart = dict(type="basket", items=[dict(offer_id="demo-mdaemon-20", quantity=1)])
        text = "Дадите скидку? Если цена устроит, выставьте счёт на корзину."
        dialog = self.ask(text, page_context=cart, request_id="conditional")
        self.assertEqual(self.ask(text, page_context=cart, request_id="conditional"), dialog)
        self.assertFalse(self.store.snapshot("buyer", self.scope)["documents"])
        held = self.ask("Выставьте счёт на корзину", page_context=cart, dialog_id=dialog["id"])
        self.assertIn("purchase_confirmation", held["decision"]["missing"])
        self.ask("Условия подтверждаю. Выставьте счёт на корзину", page_context=cart, dialog_id=dialog["id"])
        self.assertEqual(len(self.store.snapshot("buyer", self.scope)["documents"]), 1)

    def test_product_draft_and_sourced_answer_have_separate_origins(self):
        from backend.integrations import FOLLOW_UP_TEXT
        import json

        class LocalModel:
            def answer(self, text, context):
                return dict(text=json.dumps({"question": FOLLOW_UP_TEXT["edition"]}), mode="local", verified_model=True)

        self.store.engine = LocalModel()
        dialog = self.store.act("buyer", "message", {"text":"Подберите MDaemon на 20 мест. Где скачать счёт?",
                                                     "mode":"local"}, self.scope)["dialog"]
        first, second = dialog["decision"]["parts"]
        self.assertEqual(first["provenance"][0]["mode"], "local")
        self.assertTrue(second["provenance"][0]["source"].endswith("/invoices"))

    def test_accepted_condition_is_bound_to_quoted_sku(self):
        first = self.ask("MDaemon на 20 мест, цена и скидка 7%? Сколько стоит Dr.Web?")
        approved = self.ask("Да, подходит", dialog_id=first["id"])
        self.assertEqual(approved["approved_discount"], {"offer_id":"demo-mdaemon-20", "rate":5})
        cart = dict(type="basket", items=[dict(offer_id="demo-mdaemon-20", quantity=1), dict(offer_id="demo-drweb", quantity=5)])
        self.ask("Выставьте счёт на корзину", dialog_id=first["id"], page_context=cart)
        doc = self.store.snapshot("buyer", self.scope)["documents"][0]
        self.assertEqual([line["discount"] for line in doc["lines"]], [5, 0])

    def test_direct_handoff_precedes_all_other_requests(self):
        dialog = self.ask("Сколько стоит Dr.Web? Позовите менеджера.")
        self.assertEqual(dialog["decision"]["intent"], "operator")
        self.assertEqual(len(dialog["messages"]), 2)

    def test_repeated_part_is_answered_once(self):
        dialog = self.ask("Где скачать счёт? Где скачать счёт?")
        first, second = dialog["decision"]["parts"]
        self.assertEqual(first["replies"], second["replies"])
        self.assertEqual(len(dialog["messages"]), 2)
