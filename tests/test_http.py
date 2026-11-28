import http.client
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from backend.core import Store
from backend.runtime import build_store
from backend.telegram_notifications import ManagerNotifications
from server import Handler, SupportServer, access_file


class TestStandAccess(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.root = root
        self.tokens = access_file(root)
        self.server = SupportServer(("127.0.0.1", 0), Handler)
        self.server.credentials = self.tokens
        self.server.store = build_store(root / "test.sqlite", None, None, root)
        self.server.store.business_hours = lambda: True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, role=None, body=None, *, jury_guest=None, browser_session=None):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        headers = {"Content-Type": "application/json"}
        if role:
            headers["Authorization"] = "Bearer " + self.tokens[role]
        if jury_guest:
            headers["X-Stand-Guest"] = jury_guest
        if browser_session:
            headers["X-Stand-Session"] = browser_session
        client.request("GET" if body is None else "POST", path,
                       json.dumps(body) if body is not None else None, headers)
        response = client.getresponse()
        status, content, cache = response.status, response.read(), response.getheader("Cache-Control")
        client.close()
        self.assertEqual("no-store", cache)
        return status, content

    def test_telegram_pairing_and_handoff_follow_manager_session(self):
        sent = []
        store = self.server.store
        notifications = ManagerNotifications(store.storage, lambda method, payload: sent.append(payload))
        store.manager_notifications = notifications
        notifications.ready = True
        session, other = str(uuid.uuid4()), str(uuid.uuid4())
        message = {"chat": {"id": 701, "type": "private"}, "from": {"id": 701},
                   "text": "/start", "date": int(time.time())}
        notifications.receive({**message, "chat": {"id": 701, "type": "group"}}, 1)
        self.assertEqual(sent, [])
        notifications.receive(message, 2)
        pairing_message = sent.pop()
        self.assertFalse(pairing_message.get("protect_content", False))
        code = pairing_message["text"].splitlines()[2]
        connect = {"action": "telegram_connect", "payload": {"code": code}, "role": "manager1"}
        self.assertEqual(self.call("/api/action", "buyer", connect, browser_session=session)[0], 403)
        self.assertEqual(self.call("/api/action", "manager1", connect)[0], 403)
        self.assertEqual(self.call("/api/action", "manager1", connect, browser_session=session)[0], 200)
        self.assertEqual(self.call("/api/action", "manager1", connect, browser_session=other)[0], 400)
        status, data = self.call("/api/state?role=manager1", "manager1", browser_session=other)
        self.assertIsNone(json.loads(data)["telegram"]["expires"])
        handoff = {"action": "handoff", "payload": {"request_id": "once"}}
        self.assertEqual(self.call("/api/action", "jury", handoff)[0], 200)
        notifications.flush()
        self.assertEqual(sent, [])
        for _ in range(2):
            self.assertEqual(self.call("/api/action", "buyer", handoff)[0], 200)
        notifications.flush()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["chat_id"], 701)
        self.assertIn("Вам назначено новое обращение", sent[0]["text"])
        handoff["payload"]["request_id"] = "second"
        self.assertEqual(self.call("/api/action", "buyer", handoff)[0], 200)
        self.assertEqual(self.call("/api/action", "manager1", {"action": "telegram_logout"},
                                   browser_session=session)[0], 200)
        notifications.flush()
        self.assertEqual(len(sent), 1)

    def test_minimum_of_one_compound_product(self):
        name = "Kaspersky Security для Microsoft Office 365 лицензия на 1 год. Количество почтовых ящиков"
        feed = self.root / "feed.xml"
        feed.write_text(f'<yml_catalog><shop><offers><offer id="test-365" available="true">'
                        f'<name>{name}</name><price>10</price><sales_notes>Кол-во от 10 до 14 шт.</sales_notes>'
                        '</offer></offers></shop></yml_catalog>', "utf-8")
        self.server.store = Store(self.root / "case.sqlite", feed)

        def send(text, **fields):
            status, body = self.call("/api/action", "buyer", {"role": "buyer", "action": "message",
                                    "payload": {"text": text, "mode": "rules", **fields}})
            self.assertEqual(status, 200)
            return json.loads(body)["dialog"]

        d = send(name, starter="viewed", offer_id="test-365")
        self.assertNotIn("products", d["decision"]["facets"])
        self.assertNotIn("quantity", d["decision"]["facets"])
        for text in ("Эта самая минимальная версия?", "тут только один товар", "он в начале переписки", "по количеству"):
            d = send(text, dialog_id=d["id"])
            self.assertEqual(d["decision"]["evidence"], ["test-365"])
            self.assertIn("от 10 до 14", d["messages"][-1]["text"])
        self.assertEqual(d["decision"]["next_action"], "inform")
        d = send("Эта минимальная редакция?", dialog_id=d["id"])
        d = send("по функциям", dialog_id=d["id"])
        self.assertEqual(d["decision"]["next_action"], "handoff")

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

    def test_second_manager_appears_after_booking_in_jury_scope(self):
        def state(token, role):
            status, raw = self.call("/api/state?role=" + role, token)
            self.assertEqual(status, 200)
            return json.loads(raw)

        self.assertFalse(state("jury", "buyer")["manager2_active"])
        action = {"role": "buyer", "action": "message", "payload": {
            "text": "Хочу забронировать лицензии", "mode": "rules",
        }}
        status, raw = self.call("/api/action", "jury", action)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["dialog"]["assignment"], "manager2")
        self.assertTrue(state("jury", "buyer")["manager2_active"])
        self.assertFalse(state("buyer", "buyer")["manager2_active"])

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

    def test_message_reference_in_open_chat_is_scoped_to_customer(self):
        self.tokens["visitor"] = "guest." + "c" * 64

        def act(token, action, **payload):
            return self.call("/api/action", token, {"role": "guest" if token == "visitor" else "buyer",
                                                   "action": action, "payload": payload})

        state = json.loads(self.call("/api/state?role=buyer", "buyer")[1])
        order = state["orders"][0]
        license = state["licenses"][0]
        offer = next(row for row in state["offers"] if row["available"])
        status, body = act("buyer", "invoice", offer_id=offer["id"], expected_price=offer["price"],
                           quantity=offer.get("min_quantity", 1))
        self.assertEqual(status, 200, body)
        document = json.loads(body)["document"]
        dialog_id = None
        for kind, record, question in (("offer", offer, "Расскажите подробнее"),
                                       ("order", order, "Какой статус?"),
                                       ("document", document, "Где скачать?"),
                                       ("license", license, "Действует ли?")):
            status, body = act("buyer", "message", text=question, dialog_id=dialog_id or "",
                               mode="rules", reference={"type": kind, "id": record["id"]})
            self.assertEqual(status, 200, body)
            dialog = json.loads(body)["dialog"]
            self.assertEqual(dialog["id"], dialog_id or dialog["id"])
            dialog_id = dialog["id"]
            reference = dialog["messages"][-2]["reference"]
            self.assertEqual(set(reference), {"type", "id", "label"})
            self.assertEqual((reference["type"], reference["id"]), (kind, record["id"]))
            self.assertIn(record["id"], dialog["decision"]["evidence"])
            if kind == "offer":
                self.assertTrue(dialog["messages"][-1]["show_offer_details"])
            else:
                self.assertEqual(act("visitor", "message", text=question, reference={"type": kind, "id": record["id"]})[0], 403)
                self.assertEqual(act("personal", "message", text=question, reference={"type": kind, "id": record["id"]})[0], 404)
        status, body = self.call("/api/chat?role=buyer&id=" + dialog_id, "buyer")
        self.assertEqual(status, 200)
        history = json.loads(body)["dialogs"][0]["messages"]
        self.assertEqual(sum("reference" in message for message in history), 4)

    def test_guest_task_keeps_priority_over_browsed_catalog_group(self):
        self.tokens["visitor"] = "guest." + "d" * 64
        dialog_id = ""
        for text, missing in (("Помогите с выбором", "task"), ("для юриста чтобы каждый месяц чистил", "seats"), ("1", "platform")):
            starter = {"starter": "choose"} if not dialog_id else {}
            status, body = self.call("/api/action", "visitor", {"role": "guest", "action": "message", "payload": {
                "mode": "rules", "text": text, "dialog_id": dialog_id, "viewed_group_id": "unrelated-group", **starter,
            }})
            self.assertEqual(status, 200, body)
            dialog = json.loads(body)["dialog"]
            dialog_id = dialog["id"]
            self.assertEqual(dialog["decision"]["missing"], [missing])
            self.assertFalse(dialog["messages"][-1].get("offers"))

    def test_guest_basket_follow_up_uses_latest_quantity(self):
        self.tokens["visitor"] = "guest." + "a" * 64
        state = json.loads(self.call("/api/state?role=guest", "visitor")[1])
        offer = next(offer for offer in state["offers"] if offer["available"] and offer["price"])
        minimum = offer.get("min_quantity", 1)
        dialog_id = None
        for text, quantity in (("Сколько стоит корзина?", minimum), ("Сколько теперь стоит?", minimum + 1)):
            status, body = self.call("/api/action", "visitor", {"role": "guest", "action": "message", "payload": {
                "mode": "rules", "text": text, "dialog_id": dialog_id or "",
                "page_context": {"type": "basket", "items": [{"offer_id": offer["id"], "quantity": quantity}]},
            }})
            self.assertEqual(status, 200, body)
            dialog = json.loads(body)["dialog"]
            dialog_id = dialog["id"]
            self.assertEqual(dialog["decision"]["intent"], "basket")
            self.assertIn(offer["id"], dialog["decision"]["evidence"])
            self.assertIn(f"Итого по каталогу {offer['price'] * quantity:.2f}", dialog["messages"][-1]["text"])

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
        self.assertIn("Марина Тестова", json.loads(action("personal", "buyer", "download",
                                                      document_id=document_id)[1])["content"])
