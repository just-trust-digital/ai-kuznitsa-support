import tempfile
import unittest
from pathlib import Path

from backend.core import Store


class TestSupportReporting(unittest.TestCase):
    def test_counts_explicit_early_late_and_automatic_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            stand = Store(Path(directory) / "support.sqlite3")

            def say(text, dialog=None):
                payload = {"text": text, "mode": "rules"}
                if dialog:
                    payload["dialog_id"] = dialog["id"]
                return stand.act("buyer", "message", payload, "jury-check")["dialog"]

            greeting = say("Добрый день!")
            say("Позовите человека", greeting)
            buying = say("Нужен MDaemon")
            say("Оператор", buying)
            counteroffer = say("Можно скидку 9%?")
            say("Не подходит", counteroffer)
            say("Здравствуйте")
            stats = stand.snapshot("manager1", "jury-check")["handoff_stats"]
            self.assertEqual((stats["dialogs"], stats["first_request"], stats["later_request"], stats["automatic"]),
                             (3, 1, 1, 1))
