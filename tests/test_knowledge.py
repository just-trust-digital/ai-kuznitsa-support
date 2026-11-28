import tempfile
import unittest
from pathlib import Path

from backend.core import Store
from backend.integrations import IntegrationEngine
from backend.knowledge import POWER_BI_PAGE, POWER_BI_SOURCE


class KnowledgeParts(unittest.TestCase):
    def test_company_price_and_account_edi(self):
        import json
        from backend.core import offer_record
        from backend.selection import search_key
        from backend.knowledge import OFFICE_BUSINESS_NAME, OFFICE_BUSINESS_PAGE
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'test.sqlite')
            offer = offer_record("test-office", OFFICE_BUSINESS_NAME + ", электронная лицензия", 12345, "Офис")
            with store.storage.connect(write=True) as db:
                db.execute("INSERT INTO offers VALUES (?,?,?,?)", (offer['id'], search_key(offer['name']), "office", json.dumps(offer)))
            def ask(text, **extra):
                return store.act("buyer", "message", {"text": text, "mode": "rules", **extra}, "jury-edi")["dialog"]
            for buyer, status in [("buyer1", "ЭДО подключён"), ("buyer2", "ЭДО не подключён")]:
                store.act("buyer", "buyer_select", {"buyer_id": buyer}, "jury-edi")
                dialog = ask("Какая цена на Microsoft Office для дома и бизнеса 2024 для юр. лица? Есть ли у нас ЭДО?")
                answer = " ".join(m["text"] for m in dialog["messages"] if m["role"] == "assistant")
                self.assertIn("12 345", answer)
                self.assertIn(status, answer)
            # Replay treats the named company as the active fictional profile.
            named = ask('Мы из ООО «Другая компания». Есть ли у нас ЭДО?')
            self.assertEqual(named["decision"]["next_action"], "lookup")
            self.assertTrue(named["messages"][-1]["synthetic"])
            contextual = ask("Сколько стоит?", page_context=OFFICE_BUSINESS_PAGE)
            self.assertIn("12 345", contextual["messages"][-1]["text"])

    def test_known_basket_keeps_quantities_and_creates_both_documents_once(self):
        class NoModel:
            def __getattr__(self, name):
                raise AssertionError('Document handling must keep basket context out of model calls')

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite', engine=NoModel(), hybrid_engine=NoModel())
            items = [{'offer_id': 'demo-mdaemon-20', 'quantity': 2}, {'offer_id': 'demo-drweb', 'quantity': 5}]
            context = {'type': 'basket', 'items': items}
            quote = {'text': 'Подготовьте КП и счёт на всё в корзине.', 'page_context': context,
                     'mode': 'hybrid', 'request_id': 'basket-docs'}
            for _ in range(2):
                dialog = store.act('buyer', 'message', quote, 'basket-docs')['dialog']
                self.assertEqual(dialog['decision']['next_action'], 'document')
                self.assertEqual(len([m for m in dialog['messages'] if m.get('document_id')]), 2)
            docs = store.snapshot('buyer', 'basket-docs')['documents']
            self.assertEqual({d['kind'] for d in docs}, {'proposal', 'invoice'})
            self.assertEqual(len(docs), 2)
            for doc in docs:
                self.assertEqual(doc['total'], 47400.0)
                self.assertEqual([(l['offer_id'], l['quantity']) for l in doc['lines']],
                                 [(l['offer_id'], l['quantity']) for l in items])
                self.assertTrue(all(l['tax'] == 'Требует уточнения' for l in doc['lines']))
            self.assertFalse(store.snapshot('developer', 'basket-docs')['usage'].get('cloud'))

    def test_basket_asks_only_for_unknown_or_unselected_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            known = {'offer_id': 'demo-mdaemon-20', 'quantity': 2}
            unknown = {'offer_id': 'missing-demo', 'quantity': 1}
            price = {'text': 'Сколько стоит всё в корзине?', 'mode': 'rules'}
            for context, expected in (({'type': 'basket'}, ['basket_items', 'quantity']),
                                      ({'type': 'basket', 'items': []}, ['basket_items'])):
                reply = store.act('buyer', 'message', {**price, 'page_context': context}, 'unspecified')['dialog']
                self.assertEqual(reply['decision']['missing'], expected)
                self.assertEqual(reply['decision']['evidence'], [])
            mixed = {'type': 'basket', 'items': [known, unknown]}
            partial = store.act('buyer', 'message', {**price, 'page_context': mixed}, 'partial')['dialog']
            self.assertEqual(partial['decision']['missing'], ['article'])
            replies = ' '.join(m['text'] for m in partial['messages'] if m['role'] == 'assistant')
            self.assertIn('38400.00 RUB', replies)
            self.assertIn('missing-demo', replies)
            self.assertNotIn('Итого', replies)
            item_price = {**price, 'text': 'Сколько стоит эта позиция?', 'page_context': mixed}
            unselected = store.act('buyer', 'message', item_price, 'unselected')['dialog']
            self.assertEqual(unselected['decision']['missing'], ['basket_selection'])
            item_price['page_context'] = {**mixed, 'selected_offer_id': known['offer_id']}
            selected = store.act('buyer', 'message', item_price, 'selected')['dialog']
            self.assertEqual(selected['decision']['next_action'], 'inform')
            self.assertEqual(selected['decision']['evidence'], [known['offer_id']])

    def test_basket_tax_and_existing_invoice_preserve_distinct_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            context = {'type': 'basket', 'items': [{'offer_id': 'demo-mdaemon-20', 'quantity': 2}]}
            dialog = store.act('buyer', 'message', {
                'text': 'Цены с НДС? Где скачать счёт по предыдущему заказу?',
                'page_context': context, 'mode': 'rules',
            }, 'tax-and-invoice')['dialog']
            self.assertEqual(dialog['decision']['next_action'], 'handoff')
            self.assertIn('Проверка НДС', dialog['route']['reason'])
            self.assertTrue(any(m.get('source', '').endswith('/invoices') for m in dialog['messages']))
            self.assertFalse(store.snapshot('buyer', 'tax-and-invoice')['documents'])
            store.act('buyer', 'message', {
                'text': 'Пришлите счёт по предыдущему заказу.', 'page_context': context, 'mode': 'rules',
            }, 'existing-invoice')
            self.assertFalse(store.snapshot('buyer', 'existing-invoice')['documents'])

    def test_basket_keeps_unavailable_lines_out_of_the_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            items = [{'offer_id': 'demo-mdaemon-20', 'quantity': 2}, {'offer_id': 'demo-necessity', 'quantity': 1}]
            dialog = store.act('buyer', 'message', {
                'text': 'Выставьте счёт на всё в корзине.', 'page_context': {'type': 'basket', 'items': items},
                'mode': 'rules',
            }, 'unavailable-line')['dialog']
            self.assertEqual(dialog['decision']['next_action'], 'handoff')
            self.assertNotIn('Итого', ''.join(m['text'] for m in dialog['messages'] if m['role'] == 'assistant'))
            self.assertFalse(store.snapshot('buyer', 'unavailable-line')['documents'])

    def test_power_bi_family_routes_scoped_purchase_and_visuals_separately(self):
        def unexpected_call(*args):
            self.fail('Sourced family policy must take precedence over model calls')

        with tempfile.TemporaryDirectory() as tmp:
            engine = IntegrationEngine('http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen',
                                       transport=unexpected_call, llm_api='ollama')
            store = Store(Path(tmp) / 'stand.sqlite', engine=engine)
            purchase = {'text': 'Можно купить?', 'mode': 'rules'}
            generic = store.act('buyer', 'message', purchase, 'without-family')['dialog']
            self.assertNotIn(POWER_BI_SOURCE, generic['decision']['evidence'])
            sourced = store.act('buyer', 'message', {**purchase, 'page_context': POWER_BI_PAGE, 'mode': 'local'},
                               'with-family')['dialog']
            self.assertEqual(sourced['decision']['next_action'], 'handoff')
            self.assertEqual(sourced['decision']['evidence'], [POWER_BI_SOURCE])
            self.assertFalse(store.snapshot('developer', 'with-family')['usage'].get('nimble'))
            compound = store.act('buyer', 'message', {
                'text': 'Можно приобрести Microsoft Power BI? А платные визуализации можно купить у вас? '
                        'Где скачать счёт по предыдущему заказу?',
                'mode': 'rules',
            }, 'family-compound')['dialog']
            self.assertIn('цена и доступность лицензии и покупка визуализаций', compound['route']['reason'])
            self.assertEqual(len(compound['decision']['evidence']), 2)
            replies = [m for m in compound['messages'] if m['role'] == 'assistant']
            self.assertTrue(any(m.get('source') == POWER_BI_SOURCE and 'по запросу' in m['text'] for m in replies))
            self.assertTrue(any('платные визуализации' in m['text'] and not m.get('source') for m in replies))
            self.assertTrue(all(not m.get('offers') for m in replies))

    def test_power_bi_page_does_not_override_named_product_or_accept_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            purchase = {'text': 'Сколько стоит MDaemon?', 'mode': 'rules', 'page_context': POWER_BI_PAGE}
            dialog = store.act('buyer', 'message', purchase, 'other-product')['dialog']
            self.assertNotIn(POWER_BI_SOURCE, dialog['decision']['evidence'])
            self.assertTrue(any(offer['id'] == 'demo-mdaemon-20' for m in dialog['messages'] for offer in m.get('offers', [])))
            with self.assertRaisesRegex(ValueError, 'контекст страницы'):
                store.act('buyer', 'message', {**purchase, 'page_context': {**POWER_BI_PAGE, 'url': POWER_BI_SOURCE}},
                          'raw-source-url')

    def test_invoice_and_key_keep_both_instructions_and_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            result = store.act('buyer', 'message', {'text': 'Где скачать счет? Где получить ключ?', 'mode': 'rules'}, 'compound-knowledge')
            dialog = result['dialog']
            replies = [m for m in dialog['messages'] if m['role'] == 'assistant']
            self.assertEqual(len(replies), 2)
            sources = {m['source'] for m in replies}
            self.assertEqual(sources, {'https://docs.softline.com/ru-ru/lk/fulfillment', 'https://docs.softline.com/ru-ru/lk/invoices'})
            self.assertEqual(set(dialog['decision']['evidence']), sources)
            self.assertTrue(any('К продукту' in m['text'] for m in replies))
            self.assertTrue(any('найдите счёт' in m['text'] for m in replies))

    def test_tax_question_asks_for_evidence_instead_of_asserting_tax(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            dialog = store.act('buyer', 'message', {'text': 'Включен НДС?', 'mode': 'rules'}, 'tax-question')['dialog']
            self.assertEqual(dialog['decision']['next_action'], 'clarify')
            answer = dialog['messages'][-1]['text']
            self.assertIn('артикул', answer)
            self.assertNotIn('цена указана с', answer)

    def test_missing_key_keeps_invoice_help_and_requests_order_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            dialog = store.act('buyer', 'message', {'text': 'Где скачать счет? Почему не пришел ключ?', 'mode': 'rules'}, 'compound-missing-key')['dialog']
            self.assertEqual(dialog['decision']['next_action'], 'handoff')
            self.assertEqual(dialog['route']['reason'], 'Проверка исполнения заказа')
            replies = [m for m in dialog['messages'] if m['role'] == 'assistant']
            self.assertTrue(any(m.get('source', '').endswith('/invoices') for m in replies))
            self.assertTrue(any(m.get('source', '').endswith('/fulfillment') for m in replies))

    def test_existing_invoice_uses_cabinet_and_keeps_separate_document_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            lookup = store.act('buyer', 'message', {
                'text': 'Где скачать счёт по заказу?', 'mode': 'rules',
            }, 'lookup-invoice')['dialog']
            self.assertEqual(lookup['decision']['next_action'], 'inform')
            self.assertTrue(lookup['messages'][-1]['source'].endswith('/invoices'))

            compound = store.act('buyer', 'message', {
                'text': 'Где скачать счёт по заказу? Подготовьте КП на новую лицензию.', 'mode': 'rules',
            }, 'lookup-and-quote')['dialog']
            self.assertEqual(compound['decision']['intent'], 'document')
            self.assertEqual(compound['decision']['next_action'], 'clarify')
            self.assertTrue(any(m.get('source', '').endswith('/invoices') for m in compound['messages']))

    def test_shipping_help_limits_general_range_to_electronic_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            dialog = store.act('buyer', 'message', {
                'text': 'Как оформить заказ? Какие сроки доставки электронных лицензий?', 'mode': 'rules',
            }, 'checkout-and-delivery')['dialog']
            replies = [m for m in dialog['messages'] if m['role'] == 'assistant']
            self.assertEqual(dialog['decision']['next_action'], 'inform')
            self.assertEqual(len(replies), 2)
            self.assertTrue(all(m['source'].endswith('/delivery_info/') for m in replies))
            self.assertIn('Оформить заказ', replies[0]['text'])
            self.assertIn('Срок конкретной позиции указан в её карточке.', replies[1]['text'])

    def test_checkout_help_keeps_catalogue_step_for_named_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            dialog = store.act('buyer', 'message', {
                'text': 'Как купить Kaspersky?', 'mode': 'rules',
            }, 'checkout-product')['dialog']
            replies = [m for m in dialog['messages'] if m['role'] == 'assistant']
            self.assertEqual(dialog['decision']['intent'], 'selection')
            self.assertEqual(len(replies), 2)
            self.assertTrue(replies[0]['source'].endswith('/delivery_info/'))

    def test_delivery_city_and_key_timing_match_shipping_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            for text in ('Есть ли доставка в Казань?', 'Как быстро придёт ключ?'):
                dialog = store.act('buyer', 'message', {'text': text, 'mode': 'rules'}, 'shipping-phrases')['dialog']
                reply = dialog['messages'][-1]
                self.assertEqual(dialog['decision']['next_action'], 'inform', text)
                self.assertTrue(reply['source'].endswith('/delivery_info/'), text)

    def test_individual_buyers_get_checkout_help_without_product_demand(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'stand.sqlite')
            for text in ('Продаёте ли программное обеспечение физическим лицам?',
                         'Я самозанятый, могу купить лицензию?'):
                dialog = store.act('buyer', 'message', {'text': text, 'mode': 'rules'}, 'individual-buyers')['dialog']
                reply = dialog['messages'][-1]
                self.assertEqual(dialog['decision']['intent'], 'checkout', text)
                self.assertIn('корзину', reply['text'])
                self.assertNotEqual(reply['text'], 'Укажите название продукта и на сколько пользователей '
                                                   'или устройств нужна лицензия.')


if __name__ == '__main__':
    unittest.main()
