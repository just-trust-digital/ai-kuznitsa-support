import json
import subprocess
import unittest
from unittest.mock import patch

from backend.codex_cloud import MODEL, generate_public
from backend.integrations import HybridEngine


class CloudGeneratorTests(unittest.TestCase):
    def test_generator_receives_only_public_catalog_fields_after_jev(self):
        packets = []

        def choose(*args):
            return {'answers': {'route': {'choice': 'technical', 'probabilities': {'technical': 0.95}}}}

        def generate(instruction, state):
            packets.append(state)
            return {'choices': [{'message': {'content': 'Вопрос'}}],
                    'usage': {'prompt_tokens': 14, 'completion_tokens': 3}}

        engine = HybridEngine({'item': {'name': 'DemoMail', 'internal': 'private'}}, [], 'jev-latest',
                              None, MODEL, None, None, transport=choose, cloud_generator=generate)
        self.assertFalse(engine.consult('technical', 'unknown')['verified_model'])
        result = engine.consult('technical', 'item')
        self.assertTrue(result['verified_model'])
        self.assertEqual(packets, [{'intent': 'technical', 'product': {'name': 'DemoMail'},
                                    'quantity': 1, 'days': 0, 'knowledge': []}])
        self.assertEqual(result['usage']['cloud']['input_tokens'], 14)

    def test_cli_filters_child_environment_and_reads_final_answer_and_usage(self):
        packet = {'intent': 'technical', 'product': {'name': 'DemoMail'}, 'quantity': 1, 'days': 0, 'knowledge': []}
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Ответ'}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 4}}]
        completed = subprocess.CompletedProcess([], 0, '\n'.join(map(json.dumps, events)), '')
        with patch('shutil.which', return_value='codex.exe'), patch('subprocess.run', return_value=completed) as run:
            result = generate_public('Выберите вопрос.', packet)
        self.assertEqual(result['choices'][0]['message']['content'], 'Ответ')
        self.assertEqual(result['usage'], {'prompt_tokens': 10, 'completion_tokens': 4})
        self.assertIn(MODEL, run.call_args.args[0])
        self.assertNotIn('TYPESAFE_API_KEY', run.call_args.kwargs['env'])
        self.assertNotIn('SUPPORT_TELEGRAM_BOT_TOKEN', run.call_args.kwargs['env'])
        with self.assertRaises(ValueError):
            generate_public('Вопрос', dict(packet, transcript='private'))
