import unittest
import tempfile
from backend.integrations import IntegrationEngine
from backend.telegram_bridge import TelegramBridge

class IntegrationTests(unittest.TestCase):
    def test_no_configuration_no_network(self):
        self.assertFalse(IntegrationEngine().answer('private')['verified_model'])

    def test_remote_local_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            IntegrationEngine('https://example.com')

    def test_model_transport(self):
        calls = []
        def transport(url, body):
            calls.append(url)
            return {'answers': {'route': {'choice': 'billing'}}} if len(calls) == 1 else {'choices': [{'message': {'content': 'Synthetic answer'}}]}
        answer = IntegrationEngine('http://127.0.0.1:11434', 'http://127.0.0.1:8080', 'local-test', transport).answer('synthetic')
        self.assertEqual(answer['mode'], 'local')
        self.assertEqual(len(calls), 2)

    def test_telegram_authorization_and_persistent_dedup(self):
        with tempfile.TemporaryDirectory() as path:
            calls, delivered = [], []
            def bot(method, body):
                calls.append(method)
                return {'message_thread_id': 42}
            bridge = TelegramBridge(path + '/bridge.db', -1001, [7], bot, lambda *args: delivered.append(args))
            bridge.publish('d1', 'synthetic')
            update = {'update_id': 1, 'message': {'chat': {'id': -1001}, 'from': {'id': 7}, 'message_thread_id': 42, 'text': 'reply'}}
            self.assertTrue(bridge.receive(update))
            self.assertFalse(bridge.receive(update))
            update['update_id'] = 2
            update['message']['from']['id'] = 8
            self.assertFalse(bridge.receive(update))
            self.assertEqual(len(delivered), 1)
            bridge.db.close()

if __name__ == '__main__':
    unittest.main()

class HybridTests(unittest.TestCase):
    def test_both_cloud_calls_only_allowlisted_fields(self):
        from backend.integrations import HybridEngine
        calls = []
        def transport(url, payload, token):
            calls.append((url, payload))
            if len(calls) == 1:
                return {'answers': {'route': {'choice': 'billing', 'probabilities': {'billing': .99}}}}
            return {'choices': [{'message': {'content': 'Public answer'}}]}
        engine = HybridEngine({'s': {'name': 'Public', 'price': '10', 'currency': 'RUB', 'secret': 'NEVER'}}, ['https://docs.example.test'], 'jev-test', 'https://cloud.example.test/v1/chat/completions', 'test', 'key', 'key', transport)
        self.assertEqual(engine.hybrid('billing', 's')['mode'], 'hybrid')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('NEVER', str(calls))
        self.assertEqual(engine.hybrid('private customer text', 's')['mode'], 'local_required')
        self.assertEqual(len(calls), 2)

class RuntimeTests(unittest.TestCase):
    def test_store_model_and_telegram_reply(self):
        from backend.runtime import build_store
        from backend.telegram_bridge import PollingWorker
        with tempfile.TemporaryDirectory() as path:
            store = build_store(path + '/store.db', None, None, path)
            store.business_hours = lambda: True
            class Model:
                def answer(self, text, context=''):
                    return {'text': 'Synthetic model response', 'mode': 'local', 'verified_model': True}
            store.engine = Model()
            result = store.act('buyer', 'message', {'text': 'Hello', 'request_id': 'one'}, 'jury-test')
            self.assertTrue(any(m.get('model', {}).get('verified_model') for m in result['dialog']['messages']))
            dialog = result['dialog']['id']
            store.act('buyer', 'handoff', {'dialog_id': dialog}, 'jury-test')
            update = {'update_id': 7, 'message': {'chat': {'id': -1001}, 'from': {'id': 7}, 'message_thread_id': 42, 'text': 'Synthetic operator response'}}
            def bot(method, payload):
                return [update] if method == 'getUpdates' else {'message_thread_id': 42}
            bridge = TelegramBridge(path + '/bridge.db', -1001, [7], bot, None)
            worker = PollingWorker(bridge, store, 'jury-test', 'manager1')
            bridge.publish(dialog, 'synthetic')
            worker.poll_once()
            worker.poll_once()
            messages = store.snapshot('buyer', 'jury-test')['dialogs'][0]['messages']
            self.assertEqual(sum(m['text'] == 'Synthetic operator response' for m in messages), 1)
            bridge.db.close()

class RedirectTests(unittest.TestCase):
    def test_redirects_are_rejected_without_followup(self):
        import urllib.request
        import urllib.error
        from backend.integrations import NoRedirect
        req = urllib.request.Request('http://127.0.0.1:11434/test', data=b'synthetic', headers={'Authorization': 'Bearer synthetic'})
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(urllib.error.HTTPError):
                NoRedirect().redirect_request(req, None, code, 'redirect', {}, 'https://example.test/other')

class DecisionTests(unittest.TestCase):
    def test_decision_enum_and_uncertainty(self):
        from backend.integrations import parse_decision, DECISION_QUESTIONS
        self.assertEqual(len(DECISION_QUESTIONS), 2)
        result = parse_decision({'answers': {'intent': {'choice': 'availability', 'probabilities': {'availability': .97}}}})
        self.assertFalse(result['needs_clarification'])
        self.assertEqual(result['decision_intent'], 'availability')
        self.assertTrue(parse_decision({'answers': {'intent': {'choice': 'ambiguous'}}})['needs_clarification'])
        with self.assertRaises(ValueError):
            parse_decision({'answers': {'intent': {'choice': 'execute_tool'}}})

class OllamaRegression(unittest.TestCase):
    def test_consult_does_not_repeat_range_perpetual_term_or_new_purchase(self):
        import json
        from backend.selection import facets_for
        calls = []

        def transport(url, payload):
            calls.append(payload)
            if url.endswith('/systemone'):
                return {'answers': {'route': {'choice': 'technical'}, 'follow_up': {'choice': 'none'}}}
            return {'message': {'content': '{"question":"invented"}'}}

        engine = IntegrationEngine('http://127.0.0.1:11439', 'http://127.0.0.1:11434',
                                   'qwen3:8b', transport, llm_api='ollama')
        text = 'Нужна новая бессрочная лицензия MDaemon на 15–25 пользователей.'
        known = facets_for(text)
        context = json.dumps({'known_facets': known, 'follow_up_context': {'skipped': ['edition']}})
        result = engine.consult(text, context)
        self.assertEqual(known['seat_range'], [15, 25])
        self.assertEqual(known['license_operation'], 'new')
        self.assertEqual(known['license_term'], 'perpetual')
        self.assertEqual(list(calls[0]['questions']['follow_up']['criteria']), ['none'])
        self.assertIsNone(json.loads(result['text'])['question'])

    def test_nimble_selects_question_and_generator_cannot_replace_it(self):
        import json
        from backend.integrations import CONSULTATION_QUESTIONS
        calls = []
        def transport(url, payload):
            calls.append(payload)
            if url.endswith('/systemone'):
                return {'answers': {'route': {'choice': 'technical'},
                                    'follow_up': {'choice': 'duration'}}}
            return {'message': {'content': json.dumps({'question': CONSULTATION_QUESTIONS[2]})}}
        engine = IntegrationEngine('http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen3:8b', transport, llm_api='ollama')
        result = engine.consult('Synthetic', json.dumps({'known_facets': {'product': 'DemoMail', 'edition': 'Pro'}, 'offers': []}))
        self.assertEqual(calls[0]['state']['known_facets']['edition'], 'Pro')
        self.assertNotIn('edition', calls[0]['questions']['follow_up']['criteria'])
        self.assertNotIn('none', calls[0]['questions']['follow_up']['criteria'])
        self.assertEqual(calls[1]['format']['properties']['question']['enum'], [CONSULTATION_QUESTIONS[1]])
        self.assertEqual(json.loads(result['text'])['question'], CONSULTATION_QUESTIONS[1])
        self.assertEqual(result['follow_up_choice'], 'duration')

    def test_no_followup_does_not_publish_generated_question(self):
        import json
        def transport(url, payload):
            if url.endswith('/systemone'):
                return {'answers': {'route': {'choice': 'technical'}, 'follow_up': {'choice': 'none'}}}
            return {'message': {'content': '{"question":"invented"}'}}
        engine = IntegrationEngine('http://127.0.0.1:11439', 'http://127.0.0.1:11434', 'qwen3:8b', transport, llm_api='ollama')
        result = engine.consult('Synthetic', '{}')
        self.assertIsNone(json.loads(result['text'])['question'])

    def test_consult_disables_thinking_and_records_native_usage(self):
        from backend.integrations import CONSULTATION_QUESTIONS
        import json
        calls=[]
        def transport(url,payload):
            calls.append((url,payload))
            if len(calls)==1:
                return {'answers':{'route':{'choice':'technical'}}}
            return {'message':{'content':json.dumps({'question':CONSULTATION_QUESTIONS[0]})},'prompt_eval_count':19,'eval_count':8}
        result=IntegrationEngine('http://127.0.0.1:11439','http://127.0.0.1:11434','qwen3:8b',transport,llm_api='ollama').consult('Synthetic')
        self.assertTrue(result['verified_model'])
        self.assertEqual(result['usage']['local_llm']['input_tokens'],19)
        self.assertFalse(calls[1][1]['think'])
        self.assertFalse(calls[1][1]['stream'])
        self.assertEqual(calls[1][1]['format']['properties']['question']['enum'],list(CONSULTATION_QUESTIONS))
        self.assertTrue(calls[1][0].endswith('/api/chat'))

class JevProxyTests(unittest.TestCase):
    def test_proxy_missing_fails_before_network(self):
        from unittest.mock import patch
        from backend.integrations import post_jev
        with patch('backend.integrations.jev_proxy', side_effect=RuntimeError('not configured')):
            with patch('urllib.request.build_opener') as opener:
                with self.assertRaises(RuntimeError):
                    post_jev('https://api.typesafe.ai/v1/systemone', {'state':'synthetic'})
                opener.assert_not_called()
    def test_jev_uses_required_proxy_and_blocks_redirects(self):
        from unittest.mock import patch, MagicMock
        from backend.integrations import post_jev, NoRedirect
        import urllib.request
        response=MagicMock()
        response.read.return_value=b'{}'
        response.__enter__.return_value=response
        with patch('backend.integrations.jev_proxy', return_value='http://127.0.0.1:9999'):
            with patch('urllib.request.build_opener') as factory:
                factory.return_value.open.return_value.__enter__.return_value=response
                post_jev('https://api.typesafe.ai/v1/systemone', {'state':'synthetic'})
                handlers=factory.call_args.args
                self.assertTrue(any(isinstance(h,NoRedirect) for h in handlers))
                proxy=next(h for h in handlers if isinstance(h,urllib.request.ProxyHandler))
                request=urllib.request.Request('https://api.typesafe.ai/v1/systemone')
                proxy.https_open(request)
                self.assertEqual(request.host,'127.0.0.1:9999')
                self.assertEqual(request._tunnel_host,'api.typesafe.ai')
