import json
import tempfile
import unittest
from pathlib import Path

from backend.core import Store
from backend.integrations import HybridEngine
from backend.knowledge import INVOICES


class FreeformSupportTests(unittest.TestCase):
    def test_masked_question_reaches_both_models_and_generated_reply_reaches_buyer(self):
        calls = []
        def provider(url, payload, token):
            calls.append(payload)
            if "verdict" in payload.get("questions", {}):
                return {"answers": {"verdict": {"choice": "approve"}}}
            if len(calls) == 1:
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "invoices"}}}
            if len(calls) == 2:
                return {"choices": [{"message": {"content": json.dumps({"fact_ids": ["find_invoice", "fake_fact"], "premises": ["Где посмотреть", "вымышленное условие"]})}}]}
            return {"choices": [{"message": {"content": json.dumps({
                "action": "answer", "source": INVOICES, "text": "Откройте раздел счетов.",
            })}}]}

        with tempfile.TemporaryDirectory() as folder:
            adapter = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
            store = Store(Path(folder) / "test.db", hybrid_engine=adapter)
            store.runtime_config = {"hybrid": {"freeform": True, "default_for_chat": True}}
            result = store.act("buyer", "message", {"text": "Где посмотреть счета на test@example.org?"}, "qa")
            reply = result["dialog"]["messages"][-1]
            self.assertTrue(reply["model"]["published"])
            self.assertEqual(reply["text"], "Откройте раздел счетов.")
            self.assertEqual(reply["source"], INVOICES)
            self.assertEqual(len(calls), 4)
            evidence = json.loads(calls[2]["messages"][1]["content"])["guide"]
            self.assertEqual(evidence[INVOICES], "Счёт находят в «Счетах и платежах» по номеру заказа.")
            self.assertEqual(json.loads(calls[2]["messages"][1]["content"])["premises"], ["Где посмотреть"])
            self.assertNotIn("test@example.org", str(calls))
            self.assertEqual(json.loads(calls[1]["messages"][1]["content"])["suggested_sources"], [INVOICES])

    def test_order_status_excludes_key_navigation_unless_both_topics_are_requested(self):
        from backend.cloud_support import GUIDE, SOURCES
        guides = []
        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    return {"answers": {"verdict": {"choice": "approve"}}}
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "cabinet"},
                                    "second_topic": {"choice": additional}}}
            state = json.loads(payload["messages"][-1]["content"])
            guides.append(set(state["guide"]))
            reply = {"text": "Откройте нужный заказ.", "action": "answer", "source": None}
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}
        adapter = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
        for additional in ("none", "license"):
            guides.clear()
            self.assertTrue(adapter.freeform("Где посмотреть?", [], [])["verified_model"])
            expected = {SOURCES["cabinet"]}
            if additional == "license":
                expected.add(SOURCES["license"])
            self.assertEqual(guides, [set(GUIDE), expected])

    def test_verified_excerpt_can_recover_from_incorrect_topic(self):
        from backend.cloud_support import GUIDE, SOURCES
        license_url = SOURCES["license"]
        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    self.assertIn(license_url, payload["state"]["guide"])
                    return {"answers": {"verdict": {"choice": "approve"}}}
                return {"answers": {"topic": {"choice": "cabinet"}, "action": {"choice": "answer"}}}
            state = json.loads(payload["messages"][-1]["content"])
            if "suggested_sources" in state:
                self.assertNotIn(license_url, state["suggested_sources"])
                data = {"fact_ids": ["license_from_order"]}
            else:
                self.assertEqual(set(state["guide"]), {license_url})
                data = {"text": "Откройте «К продукту».", "source": license_url, "action": "answer"}
            return {"choices": [{"message": {"content": json.dumps(data)}}]}
        engine = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
        self.assertEqual(engine.freeform("Где в заказе лицензионные данные?", [], [])["source"], license_url)

    def test_attached_license_keeps_quoted_price_availability_and_followup_context(self):
        states = []

        def provider(url, payload, token):
            if "verdict" in payload.get("questions", {}):
                return {"answers": {"verdict": {"choice": "approve"}}}
            if "questions" in payload:
                states.append(payload["state"])
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "catalog"}}}
            response = {"action": "answer", "source": None, "text": "Выгрузка содержит цену 1800 ₽."}
            return {"choices": [{"message": {"content": json.dumps(response)}}]}

        with tempfile.TemporaryDirectory() as folder:
            adapter = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
            store = Store(Path(folder) / "test.db", hybrid_engine=adapter)
            store.runtime_config = {"hybrid": {"freeform": True, "default_for_chat": True}}
            with store.storage.connect(write=True) as db:
                row = db.execute("SELECT body FROM offers WHERE id='demo-drweb'").fetchone()
                offer = {**json.loads(row[0]), "listed": False, "available": False}
                db.execute("UPDATE offers SET body=? WHERE id=?", (json.dumps(offer), offer["id"]))
            result = store.act("buyer", "message", {"text": "Что известно о позиции?",
                               "reference": {"type": "offer", "id": offer["id"]}}, "qa")
            dialog = result["dialog"]
            self.assertTrue(dialog["messages"][-1]["model"]["published"])
            store.act("buyer", "message", {"dialog_id": dialog["id"], "text": "А на какой срок?"}, "qa")
            self.assertEqual(len(states), 2)
            for state in states:
                self.assertEqual(len(state["offers"]), 1)
                self.assertEqual(state["offers"][0]["price"], 1800)
                self.assertIs(state["offers"][0]["available"], False)
                self.assertEqual(state["offers"][0]["min_quantity"], 5)

    def test_semantic_review_revises_rejected_answer_once(self):
        generations = []

        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    self.assertNotIn("suggestion", payload["state"])
                    return {"answers": {"verdict": {"choice": "unsupported"}}}
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "conditions"}}}
            state = json.loads(payload["messages"][-1]["content"])
            generations.append(state)
            if len(generations) == 1:
                return {"choices": [{"message": {"content": json.dumps({"fact_ids": []})}}]}
            reply = {"text": "Исправленный ответ" if "review_issue" in state else "Черновик",
                     "action": "answer", "source": None}
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}

        adapter = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
        result = adapter.freeform("На какой срок?", [], [])
        self.assertEqual(result["text"], "Исправленный ответ")
        self.assertEqual(len(generations), 3)
        self.assertNotIn("draft", generations[-1])
        self.assertIn("general_terms", generations[1])
        self.assertEqual(generations[1]["general_terms"], generations[-1]["general_terms"])
        self.assertNotIn("suggestion", generations[-1])

    def test_truncated_reasoning_retries_without_losing_model_reply(self):
        reasoning = []

        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    return {"answers": {"verdict": {"choice": "approve"}}}
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "conditions"}}}
            reasoning.append(payload["reasoning"]["enabled"])
            if len(reasoning) == 2:
                return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
            reply = {"action": "answer", "source": None, "text": "2400 рублей."}
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]}

        adapter = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
        result = adapter.freeform("Сколько стоят две лицензии по 1200?", [], [])
        self.assertTrue(result["verified_model"])
        self.assertEqual(reasoning, [False, True, False])

    def test_transient_cloud_timeout_retries_without_repeating_the_decision(self):
        modes = []
        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    return {"answers": {"verdict": {"choice": "approve"}}}
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "store"}}}
            modes.append(payload["reasoning"]["enabled"])
            if len(modes) == 1:
                raise TimeoutError("upstream timeout")
            value = {"fact_ids": []} if len(modes) == 2 else {"action": "answer", "text": "На вашу почту.", "source": None}
            return {"choices": [{"message": {"content": json.dumps(value)}}]}
        engine = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
        reply = engine.freeform("Куда придёт ключ?", [], [])
        self.assertEqual(modes, [False, False, True])
        self.assertTrue(reply["verified_model"])
        self.assertEqual(reply["usage"]["cloud"]["calls"], 3)
        self.assertEqual(reply["usage"]["jev"]["calls"], 2)

    def test_model_receives_only_reusable_navigation_for_the_current_scope(self):
        from backend.sales_chat import remember_reply, support_topic

        question = "Как отключить уведомления?"
        navigation = "Откройте настройки уведомлений."
        seen = []

        def provider(url, payload, token):
            if "questions" in payload:
                if "verdict" in payload["questions"]:
                    return {"answers": {"verdict": {"choice": "approve"}}}
                seen.append(payload["state"].get("navigation"))
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "cabinet"}}}
            reply = {"text": navigation, "action": "answer", "source": None}
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}

        with tempfile.TemporaryDirectory() as folder:
            engine = HybridEngine({}, [], "jev", "https://example.org", "qwen", "", "", transport=provider)
            store = Store(Path(folder) / "db", hybrid_engine=engine)
            store.runtime_config = {"hybrid": {"freeform": True, "default_for_chat": True}}
            with store.storage.connect(write=True) as db:
                state = store._state(db, "team")
                remember_reply(state, {"messages": [{"role": "buyer", "text": question},
                               {"id": "message", "role": "manager1", "text": navigation}]})
                self.assertIn(support_topic(question), state["support_answers"])
                store.storage.save(db, "team", state)
            for scope in ("team", "another"):
                store.act("buyer", "message", {"text": question}, scope)
            self.assertEqual(seen, [navigation, None])
