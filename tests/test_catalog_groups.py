import json
import tempfile
import unittest
from pathlib import Path

from backend.core import Store
from backend.catalog_groups import followup


class TestCatalogGroups(unittest.TestCase):
    def test_storefront_visits_all_groups_once_and_starts_with_buyable_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "catalog.xml"
            items = [("01", "g", 0), ("02", "g", 155), ("03", "g", 90), ("04", "other", 0)]
            items += [(str(i), str(i), 100) for i in range(10, 15)]
            feed.write_text("<shop><offers>" + "".join(
                f'<offer id="{i}" group_id="{g}" available="{str(bool(price)).lower()}"><name>Item {i}</name>'
                f'<price>{price}</price></offer>' for i,g,price in items) + "</offers></shop>", encoding="utf8")
            store = Store(root / "db", feed)
            pages = [store.act("buyer", "search", {"by_product": True, "page": page, "limit": 4}, "stand")["offers"]
                     for page in (1, 2)]
            self.assertEqual([o["available"] for o in pages[0]], [True, True, True, False])
            self.assertEqual(pages[0][1]["group_price_from"], 90)
            self.assertEqual(len({o["group_id"] for page in pages for o in page}), 7)
            self.assertEqual(len(pages[0]) + len(pages[1]), 7)

    def test_comparison_uses_only_catalog_evidence(self):
        offers = [{"name": "Атлас", "params": {"Формат": ["PDF"], "Язык": ["русский"]}},
                  {"name": "Спектр", "params": {"Формат": ["SVG"], "Язык": ["русский"]}}]
        answer = followup("В чём разница?", offers)
        self.assertIn("Атлас: Формат: PDF", answer)
        self.assertIn("Спектр: Формат: SVG", answer)
        self.assertNotIn("Язык", answer)
        self.assertIn("не хватает характеристик", followup("Сравните", [{"name": "А"}, {"name": "Б"}]))

    def test_group_recommendations_respect_user_count_in_license(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "feed.xml"
            offers = [f'<offer id="{n}" group_id="mail" available="true"><name>MDaemon на {n} пользователей</name>'
                      f'<price>{n * 100}</price></offer>' for n in (6, 20)]
            feed.write_text("<shop><offers>" + "".join(offers) + "</offers></shop>", encoding="utf8")
            store = Store(root / "db", feed)
            result = store.act("buyer", "message", {"text": "Нужна лицензия на 20 мест", "mode": "rules",
                               "reference": {"type": "group", "id": "mail"}}, "stand")
            self.assertEqual([o["id"] for o in result["dialog"]["messages"][-1]["offers"]], ["20"])

    def test_group_followups_and_unrelated_guest_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "feed.xml"
            modules = ["Local Traffic Manager", "Application Acceleration Manager", "Advanced Firewall Manager"]
            offers = [f'<offer id="{i}" group_id="f5" available="false"><name>F5 {name}</name>'
                      f'<description>Описание позиции {i}</description><price>0</price><sales_notes>Кол-во от 1 до 4 шт.</sales_notes></offer>'
                      for i, name in enumerate(modules, 1)]
            feed.write_text("<yml_catalog><shop><offers>" + "".join(offers) + "</offers></shop></yml_catalog>", "utf8")
            store = Store(root / "db", feed)
            def say(text, dialog=None, **extra):
                if dialog:
                    extra["dialog_id"] = dialog["id"]
                return store.act("buyer", "message", dict(text=text, mode="rules", **extra), "stand")["dialog"]
            dialog = say("Помогите выбрать", reference={"type":"group","id":"f5"})
            dialog = say("1", dialog)
            self.assertEqual(len(dialog["messages"][-1]["offers"]), 3)
            for query, expected in (("цена?", "по запросу"), ("а чем эти три варианта отличаются?", "Описание позиции 3")):
                dialog = say(query, dialog)
                reply = dialog["messages"][-1]
                self.assertIn(expected, reply["text"])
                self.assertFalse(reply.get("offers"))
            # A stored pre-upgrade chat had no field ID for its task prompt.
            with store.storage.connect() as db:
                old = dict(id="old", status="open", assignment=None, facets={"product": "F5"},
                           viewed_group="f5", decision={"next_action":"refine_search"},
                           messages=[{"role":"assistant","text":"Можно продолжить без этого уточнения. Опишите, для чего нужна программа."},
                                     {"role":"buyer","text":"планировать маршруты"}])
                store._answer(db, {"day":1}, old, "планировать маршруты")
                self.assertEqual(old["decision"]["facets"]["task"], "планировать маршруты")
                self.assertEqual(old["decision"]["missing"], ["seats"])
                self.assertNotIn("viewed_group", old)
            for request in ("для юриста чтобы каждый месяц чистил", "собирать заявки для мастерской"):
                task = say("Помогите с выбором", starter="choose")
                task = say(request, task, viewed_group_id="f5")
                self.assertEqual(task["decision"]["facets"]["task"], request)
                self.assertEqual(task["decision"]["missing"], ["seats"])
                answer = say("1", task, viewed_group_id="f5")
                self.assertEqual(answer["decision"]["facets"]["seats"], 1)
                self.assertEqual(answer["decision"]["missing"], ["platform"])
                self.assertFalse(answer["messages"][-1].get("offers"))

    def test_group_limits_cards_and_chat_selects_exact_edition_and_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "catalog.xml"
            source = [
                ("10000", "7863", "DriveLED 4 Server", "5 до 9"),
                ("10001", "7863", "DriveLED 4 Server", "10 до 24"),
                ("10002", "7863", "DriveLED 4 Workstation", "10 до 24"),
                ("10003", "other", "DriveLED Companion", "1 до 5"),
            ]
            offers = [f'<offer id="{sku}" group_id="{group}" available="true"><name>{name}</name>'
                      f'<price>20</price><sales_notes>Кол-во от {bounds} шт.</sales_notes></offer>'
                      for sku, group, name, bounds in source]
            feed.write_text("<yml_catalog><shop><offers>" + "".join(offers) + "</offers></shop></yml_catalog>", "utf-8")
            store = Store(root / "catalog.sqlite3", feed)
            scope = "jury-demo"

            def act(action, **payload):
                return store.act("buyer", action, payload, scope)

            snapshot = store.snapshot("buyer", scope)
            self.assertEqual(len(snapshot["offers"]), 4)
            self.assertEqual(len(snapshot["catalog"]), 2)
            self.assertEqual(snapshot["catalog"][1]["group_name"], "DriveLED 4")
            self.assertEqual(snapshot["catalog"][1]["group_price_from"], 20)
            self.assertFalse(snapshot["catalog_has_more"])
            cards = act("search", query="DriveLED", limit=2, by_product=True)["offers"]
            self.assertEqual([card["group_id"] for card in cards], ["7863", "other"])
            for query in ("", "DriveLED"):
                first, last, empty = (act("search", query=query, page=page, limit=1, by_product=True)
                                      for page in (1, 2, 3))
                self.assertEqual([first["offers"][0]["group_id"], last["offers"][0]["group_id"]],
                                 ["7863", "other"] if query else ["other", "7863"])
                self.assertEqual((first["has_more"], last["has_more"], empty["offers"]), (True, False, []))
            with self.assertRaises(ValueError):
                act("search", by_product=True, page=0)

            dialog = act("message", text="Помогите подобрать лицензию",
                         reference={"type": "group", "id": "7863"}, mode="rules")["dialog"]
            self.assertEqual(dialog["messages"][0]["reference"],
                             {"type": "group", "id": "7863", "label": "DriveLED 4"})
            self.assertEqual(dialog["decision"]["missing"], ["edition"])
            dialog = act("message", text="Server", dialog_id=dialog["id"], mode="rules")["dialog"]
            self.assertEqual(dialog["decision"]["missing"], ["quantity"])
            dialog = act("message", text="15", dialog_id=dialog["id"], mode="rules")["dialog"]
            self.assertEqual(dialog["decision"]["evidence"], ["10001"])
            recommended = dialog["messages"][-1]["offers"][0]
            self.assertEqual((recommended["id"], recommended["suggested_quantity"]), ("10001", 15))

            with store.storage.connect() as db:
                self.assertEqual({json.loads(row[0])["id"] for row in store._group_rows(db, "7863")},
                                 {"10000", "10001", "10002"})
