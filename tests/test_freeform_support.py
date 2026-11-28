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
            if len(calls) == 1:
                return {"answers": {"action": {"choice": "answer"}, "topic": {"choice": "invoices"}}}
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
            self.assertEqual(len(calls), 3)
            self.assertIn("draft", json.loads(calls[2]["messages"][1]["content"]))
            self.assertNotIn("test@example.org", str(calls))
            self.assertEqual(list(json.loads(calls[1]["messages"][1]["content"])["guide"]), [INVOICES])
