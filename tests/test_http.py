import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.runtime import build_store
from server import Handler, SupportServer, access_file


class TestStandAccess(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.tokens = access_file(root)
        self.server = SupportServer(("127.0.0.1", 0), Handler)
        self.server.credentials = self.tokens
        self.server.store = build_store(root / "test.sqlite", None, None, root)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, role=None, body=None, *, jury_guest=None):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        headers = {"Content-Type": "application/json"}
        if role:
            headers["Authorization"] = "Bearer " + self.tokens[role]
        if jury_guest:
            headers["X-Stand-Guest"] = jury_guest
        client.request("GET" if body is None else "POST", path,
                       json.dumps(body) if body is not None else None, headers)
        response = client.getresponse()
        status, content, cache = response.status, response.read(), response.getheader("Cache-Control")
        client.close()
        self.assertEqual("no-store", cache)
        return status, content

    def test_token_and_role_boundaries(self):
        self.assertEqual(self.call("/api/state")[0], 401)
        self.assertEqual(self.call("/api/state?role=developer", "jury")[0], 403)
        self.assertEqual(self.call("/api/action", "buyer", {"role": "buyer", "action": "feed_import", "payload": {"day": 2}})[0], 403)
        self.assertEqual(self.call("/api/action", "jury", {"role": "manager1", "action": "template_publish"})[0], 403)
        self.assertEqual(self.call("/private/runtime/access.json")[0], 404)

    def test_jury_changes_only_sandbox(self):
        self.assertEqual(self.call("/api/action", "jury", {"role": "buyer", "action": "feed_import", "payload": {"day": 2}})[0], 200)
        for token, day in (("buyer", 1), ("jury", 2)):
            status, raw = self.call("/api/state?role=buyer", token)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["feed"]["day"], day)

    def test_jury_can_try_guest_without_client_data(self):
        visitor = "guest." + "c" * 64
        self.assertIn("guest", json.loads(self.call("/api/me", "jury")[1])["roles"])
        self.assertEqual(self.call("/api/state?role=guest", "jury")[0], 403)
        status, body = self.call("/api/state?role=guest", "jury", jury_guest=visitor)
        guest = json.loads(body)
        self.assertEqual((status, guest["role"], guest["orders"], guest["documents"]), (200, "guest", [], []))
        self.assertNotIn("customer", guest)
        self.assertNotIn("handoff_stats", guest)
        payload = {"role": "guest", "action": "message", "payload": {"text": "Какой статус заказа SL-1042?"}}
        status, body = self.call("/api/action", "jury", payload, jury_guest=visitor)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["dialog"]["decision"]["next_action"], "login")
        payload["action"] = "buyer_select"
        self.assertEqual(self.call("/api/action", "jury", payload, jury_guest=visitor)[0], 403)
        self.tokens["visitor"] = visitor
        self.assertEqual(json.loads(self.call("/api/state?role=guest", "visitor")[1])["dialogs"], [])

    def test_chat_uses_selected_catalog_offer_and_basket(self):
        state = json.loads(self.call("/api/state?role=buyer", "jury")[1])
        first, second = [offer for offer in state["offers"] if offer["available"]][:2]

        def ask(text, **context):
            payload = {"role": "buyer", "action": "message", "payload": {"text": text, "mode": "rules", **context}}
            status, body = self.call("/api/action", "jury", payload)
            self.assertEqual(status, 200)
            return json.loads(body)["dialog"]

        dialog = ask("Сколько стоит?", viewed_offer_id=first["id"])
        self.assertIn(first["id"], dialog["decision"]["evidence"])
        dialog = ask("Сколько стоит?", viewed_offer_id=second["id"], dialog_id=dialog["id"])
        self.assertIn(second["id"], dialog["decision"]["evidence"])
        dialog = ask("Сколько стоит корзина?", page_context={"type": "basket", "items": [
            {"offer_id": second["id"], "quantity": second.get("min_quantity", 1)},
        ]})
        self.assertEqual(dialog["decision"]["intent"], "basket")
        self.assertIn(second["id"], dialog["decision"]["evidence"])

    def test_guest_chat_continues_only_for_its_authenticated_customer(self):
        self.tokens.update(visitor="guest." + "a" * 64, other="guest." + "b" * 64)

        def action(who, role, name, **payload):
            return self.call("/api/action", who, {"role": role, "action": name, "payload": payload})

        for endpoint in ("/api/state?role=guest", "/api/me"):
            self.assertEqual(self.call(endpoint, "visitor")[0], 200)
        guest = json.loads(self.call("/api/state?role=guest", "visitor")[1])
        self.assertTrue(guest["offers"])
        self.assertEqual((guest["orders"], guest["documents"]), ([], []))
        for name in ("invoice", "proposal", "download", "order_status", "buyer_select"):
            self.assertEqual(action("visitor", "guest", name)[0], 403)
        status, body = action("visitor", "guest", "message", text="Какой статус заказа SL-1042?")
        self.assertEqual(status, 200)
        dialog = json.loads(body)["dialog"]
        self.assertEqual(dialog["decision"]["next_action"], "login")
        path = "/api/chat?role=guest&id=" + dialog["id"]
        self.assertEqual(json.loads(self.call(path, "other")[1])["dialogs"], [])
        self.assertEqual(action("personal", "buyer", "continue_guest_chat", dialog_id=dialog["id"],
                                guest_token=self.tokens["other"])[0], 403)
        for _ in range(2):
            self.assertEqual(action("personal", "buyer", "continue_guest_chat", dialog_id=dialog["id"],
                                    guest_token=self.tokens["visitor"])[0], 200)
        self.assertEqual(json.loads(self.call(path, "visitor")[1])["dialogs"], [])
        client = json.loads(self.call("/api/state", "personal")[1])
        self.assertEqual(client["customer"]["type"], "individual")
        self.assertEqual([o["id"] for o in client["orders"]], ["SL-1044"])
        self.assertEqual(client["dialogs"][0]["id"], dialog["id"])
        self.assertEqual(action("buyer", "buyer", "continue_guest_chat", dialog_id=dialog["id"],
                                guest_token=self.tokens["visitor"])[0], 403)
        offer = next(o for o in client["offers"] if o["available"])
        status, body = action("personal", "buyer", "invoice", offer_id=offer["id"], quantity=offer.get("min_quantity", 1),
                              expected_price=offer["price"])
        self.assertEqual(status, 200)
        document_id = json.loads(body)["document"]["id"]
        self.assertEqual(action("buyer", "buyer", "download", document_id=document_id)[0], 404)
        self.assertIn("Марина Орлова", json.loads(action("personal", "buyer", "download",
                                                      document_id=document_id)[1])["content"])
