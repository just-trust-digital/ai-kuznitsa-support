"""Temporary cloud generator using the signed-in Codex CLI on the owner's machine."""
import json
import os
import shutil
import subprocess
import tempfile
import threading


MODEL = 'gpt-6.1-sol'
_slot = threading.BoundedSemaphore(1)


def generate_public(instruction, state):
    """Called after HybridEngine constructs the public customer-data projection."""
    if (set(state) != {'intent', 'product', 'quantity', 'days', 'knowledge'}
            or set(state['product']) - {'name', 'price', 'currency'}):
        raise ValueError('Expected the public product projection')
    executable = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if not executable or not _slot.acquire(blocking=False):
        raise RuntimeError('Codex unavailable')
    try:
        with tempfile.TemporaryDirectory(prefix='softline-public-') as work:
            args = [executable, '-a', 'never', '--disable', 'shell_tool', '--disable', 'unified_exec',
                    '--disable', 'plugins', 'exec', '--ignore-user-config', '-c', 'web_search="disabled"',
                    '-c', 'project_doc_max_bytes=0', '-c', 'model_reasoning_effort="high"',
                    '-m', MODEL, '--skip-git-repo-check', '-s', 'read-only', '--ephemeral',
                    '-C', work, '--json', '-']
            allow_env = {'PATH', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'HOME', 'APPDATA',
                         'LOCALAPPDATA', 'TEMP', 'TMP', 'PATHEXT', 'COMSPEC', 'HTTP_PROXY',
                         'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY'}
            env = {k: v for k, v in os.environ.items() if k.upper() in allow_env}
            prompt = instruction + '\nОтветьте по-русски. Используйте только сведения ниже.\n' + json.dumps(state, ensure_ascii=False)
            result = subprocess.run(args, input=prompt, capture_output=True, encoding='utf-8',
                                    errors='replace', timeout=180, cwd=work, env=env,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        answer = ''
        usage = None
        for line in result.stdout.splitlines():
            event = json.loads(line)
            item = event.get('item', {})
            if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                answer = item.get('text', '')
            if event.get('type') == 'turn.completed':
                usage = event.get('usage')
        if result.returncode or not isinstance(answer, str) or not answer.strip():
            raise RuntimeError('Codex did not return an answer')
        response = {'choices': [{'message': {'content': answer}}]}
        if isinstance(usage, dict):
            response['usage'] = {'prompt_tokens': usage.get('input_tokens'),
                                 'completion_tokens': usage.get('output_tokens')}
        return response
    finally:
        _slot.release()
