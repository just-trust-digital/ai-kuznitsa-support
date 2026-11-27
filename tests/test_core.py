import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

from backend.core import Store
from backend.integrations import CONSULTATION_QUESTIONS, HybridEngine, IntegrationEngine


class CoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = Store(self.directory / "demo.db")
        self.scope = "jury-test"

    def act(self, role, action, **payload):
        return self.store.act(role, action, payload, self.scope)

    def test_chat_attachments_quotes_and_scoped_history(self):
        dialog = self.act("buyer", "message", mode="rules", attachment={"name": "check.txt", "content": "dGVzdA=="})["dialog"]
        first = dialog["messages"][0]
        ids = dict(dialog_id=dialog["id"], message_id=first["id"])
        self.assertEqual(first["attachment"], {"name": "check.txt", "size": 4})
        self.assertEqual(self.act("buyer", "chat_file", **ids)["content"], "dGVzdA==")
        with self.assertRaises(PermissionError):
            self.act("manager2", "chat_file", **ids)
        prompt = dialog["messages"][-1]
        result = self.act("buyer", "message", dialog_id=dialog["id"], text="Позовите менеджера",
                          reply_to=prompt["id"], quote="Файл добавлен", mode="rules")["dialog"]
        self.assertEqual(result["messages"][-2]["quote"]["text"], "Файл добавлен")
        self.assertEqual(self.act("manager1", "chat_file", **ids)["filename"], "check.txt")
        self.act("buyer", "end_chat", dialog_id=dialog["id"])
        with self.assertRaises(ValueError):
            self.act("manager1", "reply", dialog_id=dialog["id"], text="Проверка")
        self.act("buyer", "buyer_select", buyer_id="buyer2")
        other = self.act("buyer", "message", text="Ищу MDaemon", mode="rules")["dialog"]
        self.act("buyer", "buyer_select", buyer_id="buyer1")
        self.act("buyer", "clear_history")
        self.assertEqual(self.store.snapshot("buyer", self.scope)["dialogs"], [])
        self.assertEqual(self.store.snapshot("manager1", self.scope)["dialogs"], [])
        self.act("buyer", "buyer_select", buyer_id="buyer2")
        self.assertEqual(self.store.snapshot("buyer", self.scope)["dialogs"][0]["id"], other["id"])

    def test_import_decodes_double_escaped_catalogue_name(self):
        fixture = self.directory / "escaped.xml"
        fixture.write_text('<yml_catalog><shop><offers><offer id="one" available="true"><name>O&amp;amp;O Software GmbH DiskErase</name><vendor>O&amp;amp;O Software GmbH</vendor><price>100</price></offer></offers></shop></yml_catalog>', encoding="utf-8")
        self.store = Store(self.directory / "escaped.db", fixture)
        for variant in ("O&O", "O&amp;O"):
            offers = self.act("buyer", "search", query=variant)["offers"]
            self.assertEqual(offers[0]["name"], "O&O Software GmbH DiskErase")

    def test_catalogue_starter_keeps_exact_offer_for_price_followup(self):
        offer_id = "demo-mdaemon-20"
        first = self.act("buyer", "message", starter="viewed", offer_id=offer_id,
                         text="Расскажите о товаре", mode="rules")["dialog"]
        self.assertEqual(first["messages"][-1]["offers"][0]["id"], offer_id)
        self.act("buyer", "feed_import", day=2)
        reply = self.act("buyer", "message", dialog_id=first["id"], text="Сколько стоит?", mode="rules")
        offer = reply["dialog"]["messages"][-1]["offers"][0]
        current = self.act("buyer", "search", query="MDaemon")["offers"][0]
        self.assertEqual((offer["id"], offer["price"]), (offer_id, current["price"]))

    def test_viewed_price_requires_catalogued_license_duration(self):
        offer_id = "demo-mdaemon-20"
        first = self.act("buyer", "message", starter="viewed", offer_id=offer_id,
                         text="Расскажите о товаре", mode="rules")["dialog"]
        result = self.act("buyer", "message", dialog_id=first["id"],
                          text="Сколько стоит на два года?", mode="rules")["dialog"]
        self.assertEqual(result["decision"]["next_action"], "clarify")
        self.assertEqual(result["decision"]["missing"], ["article"])
        self.assertFalse(result["messages"][-1].get("offers"))
        self.assertNotIn("19200", result["messages"][-1]["text"])

        with self.store.storage.connect(write=True) as db:
            db.execute("UPDATE offers SET body=json_set(body, '$.params', json(?)) WHERE id=?",
                       (json.dumps({"Срок действия": ["24 мес."]}, ensure_ascii=False), offer_id))
        result = self.act("buyer", "message", dialog_id=first["id"],
                          text="Подскажите цену на два года.", mode="rules")["dialog"]
        self.assertEqual(result["decision"]["next_action"], "inform")
        self.assertEqual(result["messages"][-1]["offers"][0]["id"], offer_id)
        self.assertIn("19200", result["messages"][-1]["text"])

    def test_search_spelling_quantity_and_terms(self):
        for query in ("dr web", "Dr.Web", "drweb", "доктор веб", "doktor web"):
            self.assertEqual(self.act("buyer", "search", query=query)["offers"][0]["id"], "demo-drweb")
            chat = self.act("buyer", "message", text=query, mode="rules")["dialog"]
            self.assertEqual(chat["status"], "open")
            self.assertEqual(chat["messages"][-1]["offers"][0]["id"], "demo-drweb")
        for quantity in (4, 50):
            with self.assertRaises(ValueError):
                self.act("buyer", "proposal", offer_id="demo-drweb", quantity=quantity)
        for quantity in (5, 49):
            doc = self.act("buyer", "proposal", offer_id="demo-drweb", quantity=quantity)["document"]
            html = self.act("buyer", "download", document_id=doc["id"])["content"]
            self.assertIn("12 мес.", html)
            self.assertIn("электронная", html)

    def test_guided_replies_no_loop_or_premature_assignment(self):
        for sequence in (("антивирус", "гибко", "13", "windows"), ("для работы", "14", "windows")):
            chat = self.act("buyer", "message", starter="choose", text="Помогите с выбором", mode="rules")["dialog"]
            replies = []
            for text in sequence:
                chat = self.act("buyer", "message", dialog_id=chat["id"], text=text, mode="rules")["dialog"]
                replies.append(chat["messages"][-1]["text"])
            self.assertEqual(len(set(replies)), len(replies))
            self.assertEqual(chat["decision"]["facets"]["seats"], int(sequence[-2]))
            self.assertEqual(chat["decision"]["facets"]["platforms"], ["windows"])
            self.assertIsNone(chat["assignment"])
        self.assertEqual(self.act("buyer", "message", text="Хочу забронировать лицензии")["dialog"]["assignment"], "manager2")
        self.act("manager1", "availability", available=False)
        self.assertEqual(self.act("buyer", "message", text="Позовите менеджера")["dialog"]["status"], "queued")

    def test_invoice_idempotency_and_ownership(self):
        with self.assertRaises(ValueError):
            self.act("buyer", "invoice", offer_id="demo-mdaemon-20", quantity=3, discount=5)
        dialog = self.act("buyer", "message", text="Расскажите о товаре", starter="viewed",
                          offer_id="demo-mdaemon-20")["dialog"]
        self.act("buyer", "message", text="Можно скидку 5%?", dialog_id=dialog["id"])
        payload = dict(offer_id="demo-mdaemon-20", dialog_id=dialog["id"], quantity=3, request_id="same")
        with ThreadPoolExecutor(max_workers=4) as pool:
            confirmations = list(pool.map(lambda _: self.act("buyer", "invoice", **payload), range(4)))
        document = confirmations[0]["document"]
        self.assertTrue(all(item == confirmations[0] for item in confirmations))
        self.assertEqual(document["total"], 54720)
        self.assertIn("54720.00", self.act("buyer", "download", document_id=document["id"])["content"])
        with self.assertRaises(KeyError):
            self.act("buyer", "order_status", order_id="SL-1043")
        self.act("buyer", "buyer_select", buyer_id="buyer2")
        with self.assertRaises(KeyError):
            self.act("buyer", "download", document_id=document["id"])
        with self.assertRaises(KeyError):
            self.act("buyer", "invoice", **payload)
        independent = self.act("buyer", "invoice", **{k:v for k,v in payload.items() if k!="dialog_id"})["document"]
        self.assertNotEqual(independent["id"], document["id"])
        self.assertEqual(independent["discount"], 0)
        self.assertEqual(self.act("buyer", "order_status", order_id="SL-1043")["order"]["id"], "SL-1043")
        self.assertEqual(self.store.snapshot("buyer", "jury-other")["documents"], [])

    def test_document_snapshot_preserves_the_catalogue_and_rendered_template(self):
        templates = self.store.snapshot("developer", self.scope)["templates"]
        self.assertEqual({item["kind"] for item in templates}, {"answer", "quote", "invoice"})
        template = self.act("manager1", "template_draft", kind="quote", name="Условия",
                            text="Тест {quantity} {discount} {document_id} {article}")["template"]
        preview = self.act("manager1", "template_emulate", template_id=template["id"], text="MDaemon")
        self.assertEqual(preview["template"]["emulation"]["missing"], [])
        self.act("developer", "template_publish", template_id=template["id"])
        quote = self.act("buyer", "proposal", offer_id="demo-mdaemon-20", quantity=2)["document"]
        download = self.act("buyer", "download", document_id=quote["id"])["content"]
        for fragment in ("Коммерческое предложение", "Получатель", "срок действия КП", "способ оплаты",
                         "срок поставки", "Требует уточнения", "В т. ч. НДС", "38400.00",
                         f"Тест 2 0.0 {quote['id']} demo-mdaemon-20"):
            self.assertIn(fragment, download)
        self.assertNotIn("Реквизиты для оплаты", download)
        self.act("buyer", "feed_import", day=2)
        self.act("developer", "template_rollback", template_id="T-standard-quote")
        repeated = self.act("buyer", "download", document_id=quote["id"])
        self.assertEqual(repeated["content"], download)
        invoice = self.act("buyer", "invoice", offer_id="demo-mdaemon-20")["document"]
        bank = self.act("buyer", "download", document_id=invoice["id"])["content"]
        self.assertIn("Реквизиты для оплаты", bank)
        self.assertNotIn("Срок действия", bank)

    def test_unavailable_with_positive_price_and_atomic_bad_feed(self):
        fixture = self.directory / "catalog.xml"
        fixture.write_text('''<yml_catalog><shop><offers>
          <offer id="1" available="false"><name>MDaemon Existing</name><price>100</price></offer>
          <offer id="2" available="true"><name>MDaemon New</name><price>200</price></offer>
          </offers></shop></yml_catalog>''', encoding="utf-8")
        self.store = Store(self.directory / "new.db", fixture)
        offers = self.act("buyer", "search", query="MDaemon")["offers"]
        self.assertEqual(offers[0]["id"], "2")
        self.assertFalse(next(offer for offer in offers if offer["id"] == "1")["available"])
        result = self.act("buyer", "invoice", offer_id="1")
        self.assertIn("dialog", result)
        self.assertEqual(self.act("buyer", "feed_import", day=2)["feed"]["changes"][0]["id"], "2")
        with self.assertRaises(ValueError):
            self.act("buyer", "proposal", offer_id="2", expected_price=200)
        self.assertEqual(self.store.snapshot("buyer", "another")["feed"]["day"], 1)
        fixture.write_text("<bad>", encoding="utf-8")
        from xml.etree.ElementTree import ParseError
        with self.assertRaises(ParseError):
            self.store.import_catalog()
        self.assertEqual(self.store.snapshot("buyer", self.scope)["feed"]["count"], 2)

    def test_routing_review_and_template(self):
        first = self.act("buyer", "message", text="Просим скидку 7%")["dialog"]
        self.assertEqual(first["assignment"], "manager1")
        with self.assertRaises(PermissionError):
            self.act("manager2", "reply", dialog_id=first["id"], text="Ошибочно")
        reply = self.act("manager1", "reply", dialog_id=first["id"], text="Уточните количество")
        message = reply["dialog"]["messages"][-1]
        review = self.act("developer", "review", dialog_id=first["id"], message_id=message["id"], rating="+")
        self.assertEqual(review["review"]["message_id"], message["id"])
        flags = self.act("developer", "moderate", dialog_id=first["id"], manual_review=True)["dialog"]["flags"]
        self.assertEqual(flags, dict(manual_review=True, collect_sample=False))
        template = self.act("manager1", "template_draft", name="Ответ", text="Нашли {product_name}")["template"]
        self.act("manager1", "template_emulate", template_id=template["id"], text="MDaemon")
        with self.assertRaises(PermissionError):
            self.act("manager1", "template_publish", template_id=template["id"])
        self.act("developer", "template_publish", template_id=template["id"])
        answer = self.act("buyer", "message", text="MDaemon")["dialog"]["messages"][-1]
        self.assertEqual(answer["template_id"], template["id"])
        self.assertTrue(answer["text"].startswith("Нашли MDaemon"))

    def test_empty_archive_link_and_staff_visibility(self):
        archive = self.directory / "support.zip"
        with ZipFile(archive, "w") as handle:
            handle.writestr("buy_part_1_masked.txt", "#1 [Цена] [] Вопрос\n#2 [Заказ] [source] Заказ")
        self.store = Store(self.directory / "archive.db", samples_path=archive)
        original = self.store.snapshot("manager1", "internal")["samples"]
        self.assertEqual(len(original), 2)
        self.assertEqual(original[0]["text"], "Вопрос")
        result = self.store.act("manager1", "sample_classify",
                                {"sample_id": original[0]["id"], "label": "Счёт", "collect_sample": True},
                                "internal")
        self.assertEqual(result["sample"]["source_label"], "Цена")
        sample_id = original[0]["id"]
        self.store.act("manager1", "sample_classify",
                       {"sample_id": sample_id, "buyer_type": "IT", "license_term": "1 год"}, "internal")
        result = self.store.act("manager1", "sample_classify",
                                {"sample_id": sample_id, "delivery_term": "2 дня"}, "internal")
        self.assertEqual(result["sample"]["facets"],
                         {"buyer_type": "IT", "license_term": "1 год", "delivery_term": "2 дня"})
        with self.assertRaises(KeyError):
            self.act("manager1", "sample_classify", sample_id=original[0]["id"])

    def test_model_draft_keeps_rules_in_charge_and_retry_uses_cache(self):
        class LocalModel:
            calls = 0

            def answer(self, text, context):
                self.calls += 1
                return dict(text="Модель предлагает проверить редакцию", mode="local", verified_model=True)

        local = LocalModel()
        self.store = Store(self.directory / "engine.db", engine=local)
        payload = dict(text="Нужен MDaemon на 20 пользователей", request_id="cache")
        first = self.act("buyer", "message", **payload)
        self.assertEqual(first, self.act("buyer", "message", **payload))
        self.assertEqual(local.calls, 1)
        answer = first["dialog"]["messages"][-1]
        self.assertEqual(answer["mode"], "rules")
        self.assertEqual(answer["model"]["mode"], "local")
        self.assertNotIn("draft", answer["model"])
        self.assertNotIn("Модель предлагает", answer["text"])
        handoff = self.act("buyer", "message", text="Скидка 12% на MDaemon")["dialog"]
        self.assertEqual(handoff["assignment"], "manager1")
        self.assertNotIn("draft", handoff["messages"][-1]["model"])

    def test_first_turn_clarifies_and_keeps_security_requirements(self):
        result = self.act("buyer", "message", text="Хотим защитить почту и файлы на 50 сотрудников")
        dialog = result["dialog"]
        self.assertEqual(dialog["decision"]["next_action"], "clarify")
        self.assertEqual(dialog["decision"]["missing"], ["infrastructure", "budget"])
        self.assertFalse(dialog["decision"]["mandatory"])
        result = self.act("buyer", "message", dialog_id=dialog["id"],
                          text="Почта Exchange, рабочие станции Windows, бюджет до 100 000 руб.")
        decision = result["dialog"]["decision"]
        self.assertEqual(decision["turn"], 2)
        self.assertEqual(decision["facets"]["seats"], 50)
        self.assertEqual(decision["next_action"], "shortlist")
        self.assertIn("demo-kaspersky-50", decision["evidence"])

    def test_existing_request_new_document_and_manual_price_use_distinct_routes(self):
        first = self.act("buyer", "message", text="Есть новости по прежнему запросу цены?")["dialog"]
        self.assertEqual(first["decision"]["missing"], ["request_number"])
        existing = self.act("buyer", "message", dialog_id=first["id"], text="REF-1234")["dialog"]
        self.assertEqual(existing["decision"]["facets"]["request_number"], "REF-1234")
        self.assertEqual(existing["decision"]["next_action"], "handoff")
        document = self.act("buyer", "message", text="Нужен счёт на семь лицензий MDaemon по реквизитам ООО Пример")["dialog"]
        self.assertEqual(document["decision"]["intent"], "document")
        self.assertEqual(document["decision"]["facets"]["quantity"], 7)
        self.assertIn("article", document["decision"]["missing"])
        manual = self.act("buyer", "price_request", offer_id="demo-mdaemon-20", request_id="manual")
        self.assertEqual(manual["dialog"]["decision"]["evidence"], ["demo-mdaemon-20"])
        self.assertEqual(manual["dialog"]["decision"]["next_action"], "handoff")
        self.assertEqual(self.store.snapshot("buyer", self.scope)["documents"], [])

    def test_reduced_price_is_imported_and_mentioned_without_changing_total(self):
        fixture = self.directory / "sale.xml"
        fixture.write_text("""<yml_catalog><shop><offers>
          <offer id="sale" available="true"><name>MDaemon Sale</name><price>100</price><oldprice>200</oldprice></offer>
          <offer id="equal" available="true"><name>MDaemon Equal</name><price>100</price><oldprice>100</oldprice></offer>
          <offer id="zero" available="true"><name>MDaemon Zero</name><price>100</price><oldprice>0</oldprice></offer>
          <offer id="bad" available="true"><name>MDaemon Bad</name><price>100</price><oldprice>unknown</oldprice></offer>
          <offer id="unavailable" available="false"><name>MDaemon Unavailable</name><price>100</price><oldprice>200</oldprice></offer>
          </offers></shop></yml_catalog>""", encoding="utf-8")
        self.store = Store(self.directory / "sale.db", fixture)
        offers = self.act("buyer", "search", query="MDaemon")["offers"]
        by_id = {item["id"]: item for item in offers}
        self.assertEqual(by_id["sale"]["old_price"], 200)
        self.assertEqual([key for key, item in by_id.items() if Store._reduced_price(item)], ["sale"])
        dialog = self.act("buyer", "message", text="Нужен MDaemon")['dialog']
        self.assertIn("MDaemon Sale", dialog["messages"][-1]["text"])
        result = self.act("buyer", "proposal", offer_id="sale", quantity=2, dialog_id=dialog["id"])
        self.assertEqual(result["document"]["total"], 200)
        snapshot = self.store.snapshot("buyer", self.scope)
        saved = next(item for item in snapshot["dialogs"] if item["id"] == dialog["id"])
        self.assertIn("цена этой позиции снижена", saved["messages"][-1]["text"])

    def test_catalogue_uses_delivery_version_and_platform_fields(self):
        fixture = self.directory / "params.xml"
        fixture.write_text('''<yml_catalog><shop><offers>
          <offer id="1" available="true"><name>Windows Server 2022 Standard</name><price>100</price>
            <param name="Тип поставки">OEM</param><param name="Совместимая архитектура/платформа">x64</param></offer>
          <offer id="2" available="true"><name>Windows Server 2022 Standard</name><price>200</price>
            <param name="Тип поставки">Коробочная версия</param><param name="Совместимая архитектура/платформа">x64</param></offer>
          <offer id="3" available="true"><name>Windows Server 2019 Standard</name><price>100</price>
            <param name="Тип поставки">Коробочная версия</param><param name="Совместимая архитектура/платформа">ARM64</param></offer>
          </offers></shop></yml_catalog>''', encoding="utf-8")
        self.store = Store(self.directory / "params.db", fixture)
        reply = self.act("buyer","message", text="Нужен Windows Server 2022 Standard под x64, коробочный, не OEM")
        offers = reply["dialog"]["messages"][-1]["offers"]
        self.assertEqual([item["id"] for item in offers], ["2"])
        self.assertEqual(offers[0]["params"]["Тип поставки"], ["Коробочная версия"])
        mobile = self.act("buyer", "message", text="Какие лицензии РЕД ОС подойдут для 500 планшетов?")["dialog"]
        self.assertEqual(mobile["decision"]["facets"]["seats"], 500)
        self.assertIn("architecture", mobile["decision"]["missing"])

    def test_core_licence_excludes_add_ons_and_ineligible_offers_even_after_model_hint(self):
        fixture = self.directory / "eligible.xml"
        fixture.write_text('''<yml_catalog><shop><offers>
          <offer id="addon" available="true"><name>Contoso защитное дополнение для Office 2023</name><price>20</price>
            <param name="Тип лицензии">Дополнение</param></offer>
          <offer id="foreign" available="true"><name>Microsoft Office 2023</name><price>1</price>
            <param name="Тип организации">Для иностранных организаций</param></offer>
          <offer id="local" available="true"><name>Microsoft Office 2023</name><price>200</price>
            <param name="Тип организации">Коммерческая</param></offer>
          </offers></shop></yml_catalog>''', encoding="utf-8")

        class NoisyRouter:
            def answer(self, text, context):
                return dict(mode="local", verified_model=True, decision_intent="invoice_quote",
                            decision_version="test", needs_clarification=False, text="")

        self.store = Store(self.directory / "eligible.db", fixture, engine=NoisyRouter())
        search = self.act("buyer", "search", query="Office 2023")
        self.assertEqual([offer["id"] for offer in search["offers"]], ["local"])
        result = self.act("buyer", "message", text="Сколько стоит Office 2023 для коммерческой компании?")["dialog"]
        self.assertEqual(result["decision"]["next_action"], "shortlist")
        self.assertEqual(result["decision"]["evidence"], ["local"])
        self.assertEqual(self.store.snapshot("buyer", self.scope)["documents"], [])
        explicit = self.act("buyer", "message", text="Выставьте счёт на Office 2023, три лицензии.")["dialog"]
        self.assertEqual(explicit["decision"]["intent"], "document")
        self.assertEqual(explicit["decision"]["facets"]["quantity"], 3)

    def test_support_promotions_and_compatibility_have_specialized_routes(self):
        compatibility = self.act("buyer", "message", text="Можно ли подключить AnyDesk к 45 устройствам?")["dialog"]
        self.assertEqual(compatibility["decision"]["intent"], "compatibility")
        self.assertEqual(compatibility["decision"]["facets"]["seats"], 45)
        self.assertIn("concurrent_use", compatibility["decision"]["missing"])
        support = self.act("buyer", "message", text="Какая техническая поддержка предусмотрена для Альт Сервер 11?")["dialog"]
        self.assertEqual(support["decision"]["missing"], ["support_plan"])
        promotion = self.act("buyer", "message", text="Мне положена индивидуальная скидка на мой офисный пакет?")["dialog"]
        self.assertEqual(promotion["decision"]["intent"], "promotion_terms")
        self.assertTrue(promotion["decision"]["mandatory"])

    def test_software_family_vendor_and_platform_are_required_before_shortlist(self):
        fixture = self.directory / "proof.xml"
        fixture.write_text('''<yml_catalog><shop><offers>
          <offer id="gateway" available="true"><name>КриптоПро CSP 5.1 NGate для Linux</name><vendor>КриптоПро</vendor><price>20</price>
            <param name="Тип продукта">оборудование</param></offer>
          <offer id="module" available="true"><name>КриптоПро CSP 5.1 для Linux</name><vendor>КриптоПро</vendor><price>20</price>
            <param name="Тип продукта">софт</param><param name="Тип лицензии">Модуль</param></offer>
          <offer id="mention" available="true"><name>КриптоПро CSP 5.1 перенос с Linux</name><vendor>КриптоПро</vendor><price>20</price>
            <param name="Тип продукта">софт</param><param name="Совместимая архитектура/платформа">Windows 11</param></offer>
          <offer id="csp" available="true"><name>КриптоПро CSP 5.1</name><vendor>КриптоПро</vendor><price>90</price>
            <param name="Тип продукта">софт</param><param name="Тип лицензии">Полная версия</param>
            <param name="Совместимая архитектура/платформа">Linux</param></offer>
          <offer id="mail" available="true"><name>Kaspersky Endpoint Security почтовый шлюз</name>
            <vendor>Contoso</vendor><price>10</price><param name="Тип продукта">софт</param>
            <param name="Назначение">Почта</param><param name="К-во узлов">7</param></offer>
          <offer id="server" available="true"><name>Kaspersky Endpoint Security лицензия для сервера</name>
            <vendor>Kaspersky</vendor><price>10</price><param name="Тип продукта">софт</param>
            <param name="Совместимая архитектура/платформа">Windows Server</param><param name="К-во узлов">7</param></offer>
          <offer id="workstation" available="true"><name>Kaspersky Endpoint Security для бизнеса</name>
            <vendor>Kaspersky</vendor><price>110</price><param name="Тип продукта">софт</param>
            <param name="Назначение">Защита рабочих станций</param><param name="К-во узлов">7</param>
            <param name="Совместимая архитектура/платформа">Windows 11</param></offer>
          </offers></shop></yml_catalog>''', encoding="utf-8")
        local_evidence = []
        cloud_skus = []

        class Local:
            def answer(self, text, context):
                local_evidence.append(context)
                return dict(text="Уточните операционную систему.", mode="local", verified_model=True)

        class Hybrid:
            catalog = {"csp": {"name": "КриптоПро CSP"}, "mail": {"name": "Касперский"}}

            def hybrid(self, intent, sku):
                cloud_skus.append(sku)
                return dict(text="Уточните операционную систему.", mode="hybrid", verified_model=True)

        self.store = Store(self.directory / "proof.db", fixture, engine=Local(), hybrid_engine=Hybrid())
        for query, expected in (("КриптоПро CСП 5.1 для Linux", "csp"),
                                ("Kaspersky Endpoint Security для 7 ПК на Windows", "workstation")):
            search = self.act("buyer", "search", query=query)
            self.assertEqual([offer["id"] for offer in search["offers"]], [expected])
            for mode in ("rules", "local", "hybrid"):
                message = self.act("buyer", "message", text=query, mode=mode)["dialog"]["messages"][-1]
                self.assertEqual([offer["id"] for offer in message["offers"]], [expected])
        for text in ("CSP Linux", "Касперский для 7 ПК", "Kaspersky Endpoint Security для 7 ПК на Linux"):
            result = self.act("buyer", "message", text=text, mode="hybrid")["dialog"]
            self.assertEqual(result["decision"]["next_action"], "clarify")
            self.assertFalse(result["messages"][-1].get("offers"))
        for query, expected in (
            ("CSP для Linux, артикул csp", ["csp"]),
            ("CSP для Linux, артикул gateway", []),
            ("Касперский для 7 ПК, артикул workstation", ["workstation"]),
            ("Касперский для 7 ПК, артикул mail", []),
        ):
            self.assertEqual([item["id"] for item in self.act("buyer", "search", query=query)["offers"]], expected)
            message = self.act("buyer", "message", text=query)["dialog"]["messages"][-1]
            self.assertEqual([item["id"] for item in message.get("offers", [])], expected)
        self.assertEqual(cloud_skus, ["csp"])
        self.assertFalse(any("NGate" in evidence or "почтовый шлюз" in evidence for evidence in local_evidence))

    def test_contact_markers_never_select_catalogue_products(self):
        signs = "\n[email]\n[телефон]\ncontact: support@example.test\nhttps://example.test/MDaemon\nС уважением,\nOffice"
        self.assertEqual(self.act("buyer", "search", query=signs)["offers"], [])
        for text, product in (("Что подобрать для компьютеров?" + signs, None),
                              ("Нужен кас\nперский для компьютеров. Лицензий нужно 8." + signs, "kaspersky")):
            dialog = self.act("buyer", "message", text=text, mode="rules")["dialog"]
            self.assertEqual(dialog["decision"]["next_action"], "clarify")
            self.assertEqual(dialog["decision"]["facets"].get("product"), product)
            self.assertFalse(dialog["messages"][-1].get("offers"))
            if product:
                self.assertEqual(dialog["decision"]["facets"]["quantity"], 8)
        bundle = self.act("buyer", "message", mode="rules",
                          text="КриптоПро CSP и Рутокен входят в состав заказа. Укажите стоимость и срок поставки.")["dialog"]
        self.assertEqual(bundle["decision"]["next_action"], "handoff")
        self.assertIn("рутокен", bundle["decision"]["facets"]["products"])
        self.assertFalse(bundle["messages"][-1].get("offers"))

    def test_explicit_handoff_claim_and_reassignment(self):
        for manager in ("manager1", "manager2"):
            self.act(manager, "availability", available=False)
        dialog = self.act("buyer", "message", text="Подключите менеджера по скидке 5% на MDaemon")["dialog"]
        self.assertTrue(dialog["decision"]["mandatory"])
        self.assertEqual(dialog["decision"]["intent"], "operator")
        queue = self.store.snapshot("manager1", self.scope)
        self.assertEqual(queue["dialogs"], [])
        self.assertEqual(queue["queue"][0]["id"], dialog["id"])
        self.assertNotIn("messages", queue["queue"][0])
        self.act("manager1", "claim", dialog_id=dialog["id"])
        with self.assertRaises(PermissionError):
            self.act("manager2", "claim", dialog_id=dialog["id"])
        reassigned = self.act("manager1", "reassign", dialog_id=dialog["id"], manager_id="manager2")
        self.assertEqual(reassigned["dialog"]["assignment"], "manager2")
        self.assertEqual(self.store.snapshot("manager1", self.scope)["dialogs"], [])
        copilot = self.act("manager2", "copilot", dialog_id=dialog["id"], mode="hybrid")
        self.assertEqual(copilot["mode"], "rules")
        self.assertEqual(copilot["model"]["requested_mode"], "hybrid")
        self.assertFalse(copilot["model"]["verified_model"])

    def test_cached_reply_and_copilot_recheck_assignment(self):
        dialog = self.act("buyer", "handoff", text="MDaemon")["dialog"]
        fields = dict(dialog_id=dialog["id"], request_id="original")
        self.act("manager1", "reply", text="Ответ", **fields)
        self.act("manager1", "copilot", **fields)
        self.act("manager1", "reassign", dialog_id=dialog["id"], manager_id="manager2")
        with self.assertRaises(PermissionError):
            self.act("manager1", "reply", text="Ответ", **fields)
        with self.assertRaises(PermissionError):
            self.act("manager1", "copilot", **fields)

    def test_automatic_review_and_replay_are_staff_only_and_do_not_mutate_chat(self):
        dialogue = self.act("buyer", "message", text="Подскажите про MDaemon. Почта audit@example.invalid.")["dialog"]
        queue = self.store.snapshot("manager1", self.scope)["review_queue"]
        entry = next(item for item in queue if item["dialog_id"] == dialogue["id"])
        self.assertNotIn("audit@example.invalid", entry["question"])
        self.assertNotIn("offers", entry)
        fields = {key: entry[key] for key in ("dialog_id", "message_id", "version")}
        review = self.act("manager1", "review", **fields, rating="-", note="Уточнить срок", request_id="review")["review"]
        self.assertEqual(review["answer"], entry["answer"])
        self.assertEqual(review["version"], entry["version"])
        self.assertEqual(self.store.snapshot("manager2", self.scope)["review_queue"][0]["previous_rating"], "-")
        self.assertEqual(self.store.snapshot("buyer", self.scope)["review_queue"], [])
        self.assertEqual(self.store.snapshot("buyer", self.scope)["reviews"], [])
        before = self.store.snapshot("buyer", self.scope)
        template = self.act("manager1", "template_draft", text="Уточните дату начала для {product_name}.")["template"]
        self.act("manager1", "template_emulate", template_id=template["id"], text="MDaemon")
        self.act("developer", "template_publish", template_id=template["id"])
        reply = self.act("manager2", "review_replay", **fields)["replay"]
        self.assertEqual(reply["version"], entry["version"])
        self.assertIn("Уточните дату начала", reply["answer"])
        self.assertNotEqual(reply["answer"], entry["answer"])
        after = self.store.snapshot("buyer", self.scope)
        self.assertEqual(before["dialogs"], after["dialogs"])
        self.assertEqual(before["documents"], after["documents"])
        with self.assertRaises(ValueError):
            self.act("manager1", "review_replay", **{**fields, "version": "wrong"})
        with self.assertRaises(PermissionError):
            self.act("buyer", "review_replay", **fields)
        self.act("buyer", "handoff", dialog_id=dialogue["id"])
        self.assertEqual(self.store.snapshot("manager2", self.scope)["review_queue"], [])
        with self.assertRaises(PermissionError):
            self.act("manager2", "review_replay", **fields)
        self.act("manager1", "reassign", dialog_id=dialogue["id"], manager_id="manager2")
        with self.assertRaises(PermissionError):
            self.act("manager1", "review", **fields, rating="-", note="Уточнить срок", request_id="review")

    def test_model_cannot_repeat_known_number_of_users(self):
        dialog = {'facets': {'seat_range': [15, 25]}, 'messages': [
            {'text': 'Исходный ответ', 'offers': [{'id': 'demo'}], 'decision': {'next_action': 'shortlist'}},
        ]}
        model = {'verified_model': True, 'route': 'technical',
                 'text': json.dumps({'question': CONSULTATION_QUESTIONS[2]})}
        Store._publish_consultation(dialog, model)
        self.assertEqual(dialog['messages'][-1]['text'], 'Исходный ответ')

    def test_published_license_question_carries_numeric_or_skipped_reply(self):
        calls = []

        def transport(url, payload):
            if url.endswith('/systemone'):
                calls.append(payload)
                choices = payload['questions']['follow_up']['criteria']
                choice = 'seats' if 'seats' in choices else next(iter(choices))
                return {'answers': {'route': {'choice': 'technical'}, 'follow_up': {'choice': choice}}}
            return {'message': {'content': '{}'}}

        feed = self.directory / 'license.xml'
        feed.write_text('<yml_catalog><shop><offers>' + ''.join(
            f'<offer id="md-{count}" available="true">'
            f'<name>MDaemon Standard на {count} пользователей на 1 год</name><price>15000</price></offer>'
            for count in (10, 20, 50)
        ) + '</offers></shop></yml_catalog>', encoding='utf-8')
        self.store = Store(self.directory / 'license.db', feed, engine=IntegrationEngine(
            'http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen3:8b', transport, llm_api='ollama',
        ))

        for reply in ('20.', 'Пока не знаю'):
            with self.subTest(reply=reply):
                first = self.act('buyer', 'message', mode='local',
                                 text='Нужна новая лицензия MDaemon Standard на 1 год.')['dialog']
                self.assertTrue(first['messages'][-1]['model']['published'])
                self.assertEqual(first['decision']['missing'], ['seats'])
                last = self.act('buyer', 'message', mode='local', dialog_id=first['id'], text=reply)['dialog']
                request = calls[-1]
                self.assertFalse(last['messages'][-1]['model'].get('published'))
                self.assertEqual(list(request['questions']['follow_up']['criteria']), ['none'])
                if reply == '20.':
                    self.assertEqual(request['state']['known_facets']['seats'], 20)
                    self.assertEqual(last['decision']['facets']['seats'], 20)
                    self.assertEqual([offer['id'] for offer in last['messages'][-1]['offers']], ['md-20'])
                else:
                    self.assertEqual(request['state']['evidence']['follow_up_context']['skipped'], ['seats'])
                    with self.store.storage.connect() as db:
                        saved = self.store._state(db, self.scope)['dialogs'][last['id']]
                        self.assertTrue(saved['optional_answers']['seats'])

        outside = self.act('buyer', 'message', mode='rules', text='20.')['dialog']
        self.assertNotIn('seats', outside['decision']['facets'])

    def test_short_reply_after_question_saved_by_previous_version(self):
        dialog = {'decision': {'next_action': 'shortlist', 'missing': []}, 'messages': [
            {'role': 'assistant', 'text': CONSULTATION_QUESTIONS[2] + '\nПозиция из каталога.',
             'model': {'published': True}},
        ]}
        self.assertEqual(Store._incoming_facets('20', dialog), ({'seats': 20}, None))
        dialog['messages'].append({'role': 'buyer', 'text': '20'})
        self.assertEqual(Store._incoming_facets('20', dialog), ({'seats': 20}, None))

    def test_switching_product_does_not_reuse_old_license_terms(self):
        calls = []

        def transport(url, payload):
            if url.endswith('/systemone'):
                calls.append(payload)
                criteria = payload['questions']['follow_up']['criteria']
                choice = 'seats' if 'seats' in criteria else next(iter(criteria))
                return {'answers': {'route': {'choice': 'technical'}, 'follow_up': {'choice': choice}}}
            return {'message': {'content': json.dumps({'question': CONSULTATION_QUESTIONS[2]})}}

        self.store = Store(self.directory / 'switch.db', engine=IntegrationEngine(
            'http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen3:8b', transport, llm_api='ollama',
        ))
        first = self.act('buyer', 'message', mode='rules',
                         text='Подбираю MDaemon на 20 пользователей на 2 года. Нужна новая лицензия.')['dialog']
        args = {'mode': 'local', 'dialog_id': first['id']}
        self.act('buyer', 'message', text='Именно MDaemon Messaging Server.', **args)
        self.act('buyer', 'message', text='Теперь хочу подобрать Dr.Web.', **args)
        self.act('buyer', 'message', text='Лицензия нужна на 1 год.', **args)

        refinement, initial, continuation = calls
        self.assertEqual(refinement['state']['known_facets']['seats'], 20)
        self.assertNotIn('seats', refinement['questions']['follow_up']['criteria'])
        for call in (initial, continuation):
            known = call['state']['known_facets']
            self.assertEqual(known['product'], 'dr.web')
            self.assertNotIn('seats', known)
            self.assertNotIn('license_operation', known)
            self.assertIn('seats', call['questions']['follow_up']['criteria'])
        self.assertNotIn('duration', initial['state']['known_facets'])
        self.assertEqual(continuation['state']['known_facets']['billing_period'], 'yearly')

    def test_model_does_not_replace_price_from_viewed_offer(self):
        calls = []

        def transport(url, payload):
            calls.append(payload)
            if url.endswith('/systemone'):
                return {'answers': {'route': {'choice': 'technical'}, 'follow_up': {'choice': 'none'}}}
            return {'message': {'content': json.dumps({'question': CONSULTATION_QUESTIONS[0]})}}

        offer_id = 'demo-mdaemon-20'
        self.store = Store(self.directory / 'viewed.db', engine=IntegrationEngine(
            'http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen3:8b', transport, llm_api='ollama',
        ))
        first = self.act('buyer', 'message', starter='viewed', offer_id=offer_id,
                         text='Расскажите о товаре', mode='rules')['dialog']
        result = self.act('buyer', 'message', dialog_id=first['id'], text='Сколько стоит?', mode='local')['dialog']
        answer = result['messages'][-1]
        self.assertEqual(list(calls[0]['questions']['follow_up']['criteria']), ['none'])
        self.assertEqual(answer['decision']['evidence'], [offer_id])
        self.assertEqual(answer['mode'], 'rules')
        self.assertIn('19200', answer['text'])
        self.assertFalse(answer['model'].get('published'))

    def test_configured_adapters_publish_only_grounded_consultations(self):
        captured = []

        def transport(url, payload, token=None):
            captured.append((url, payload))
            if url.endswith("/systemone"):
                return dict(answers=dict(route=dict(choice="technical", probabilities=dict(technical=.99))))
            question = CONSULTATION_QUESTIONS[0 if "127.0.0.1" in url else 3]
            content = json.dumps(dict(question=question), ensure_ascii=False)
            return dict(choices=[dict(message=dict(content=content))])

        sku = "demo-mdaemon-20"
        self.store = Store(self.directory / "configured.db",
                           engine=IntegrationEngine("http://127.0.0.1:8001", "http://127.0.0.1:8002",
                                                    "qwen", transport=transport),
                           hybrid_engine=HybridEngine({sku: dict(name="MDaemon", price=19200, currency="RUB")},
                                                      [], "jev", "https://model.invalid/chat/completions",
                                                      "cloud", "", "", transport=transport))
        for mode, question in zip(("local", "hybrid"), (CONSULTATION_QUESTIONS[0], CONSULTATION_QUESTIONS[3])):
            result = self.act("buyer", "message", text="Нужен MDaemon на 20 пользователей", mode=mode)
            dialog = result["dialog"]
            answer = dialog["messages"][-1]
            self.assertEqual(answer["mode"], mode)
            self.assertTrue(answer["text"].startswith(question))
            self.assertIn(sku, answer["text"])
            self.assertEqual(answer["evidence"]["offer_id"], sku)
            developer = self.act("developer", "moderate", dialog_id=dialog["id"], note="PRIVATE REVIEW")
            self.assertEqual(developer["dialog"]["note"], "PRIVATE REVIEW")
            buyer = self.store.snapshot("buyer", self.scope)
            persisted = next(item for item in buyer["dialogs"] if item["id"] == dialog["id"])
            self.assertEqual(persisted["messages"][-1], answer)
            self.assertNotIn("PRIVATE REVIEW", json.dumps(buyer))
        self.assertEqual(len(captured), 4)


if __name__ == "__main__":
    unittest.main()
