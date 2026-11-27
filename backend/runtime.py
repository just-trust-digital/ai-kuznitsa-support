"""Runtime wiring; all network work happens outside Store transactions."""
import json
import os
import threading
from pathlib import Path
from .core import Store
from .integrations import IntegrationEngine, HybridEngine
from .telegram_bridge import TelegramBridge, PollingWorker


def build_store(db, feed, corpus, runtime_dir):
    path = Path(runtime_dir) / 'integrations.json'
    config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    local = config.get('local', {})
    engine = IntegrationEngine(local.get('nimble_url'), local.get('llm_url'), local.get('model'), llm_api=local.get('llm_api', 'openai'))
    hybrid = config.get('hybrid', {})
    hybrid_engine = None
    if hybrid.get('enabled'):
        from .codex_cloud import generate_public
        provider = hybrid.get('cloud_backend', 'api')
        if provider not in ('api', 'codex'):
            raise ValueError('Unknown cloud backend')
        hybrid_engine = HybridEngine(
            hybrid['public_catalog'], hybrid.get('kb_urls', []), hybrid['jev_model'],
            hybrid.get('cloud_url'), hybrid.get('cloud_model'), os.environ.get('TYPESAFE_API_KEY'),
            os.environ.get('SUPPORT_CLOUD_API_KEY'), cloud_generator=generate_public if provider == 'codex' else None)
    store = Store(db, feed, corpus, engine=engine, hybrid_engine=hybrid_engine)
    store.runtime_config = config
    return store


def start_forum(store, runtime_dir):
    config = getattr(store, 'runtime_config', {}).get('telegram', {})
    if not config.get('chat_id') or not os.environ.get('SUPPORT_TELEGRAM_BOT_TOKEN'):
        return lambda: None
    # Only explicitly synthetic jury workspaces may enter Telegram.
    scope = config.get('scope', '')
    if not scope.startswith('jury') or config.get('synthetic_only') is not True:
        raise ValueError('Telegram requires an explicitly synthetic jury workspace')
    roles = {int(k): v for k, v in config.get('operators', {}).items()}
    if not roles or any(v not in ('manager1', 'manager2') for v in roles.values()):
        raise ValueError('Telegram user-to-manager mapping is required')
    token = os.environ['SUPPORT_TELEGRAM_BOT_TOKEN']
    from .integrations import post
    def call(method, payload):
        try:
            result = post('https://api.telegram.org/bot' + token + '/' + method, payload)
            if not result.get('ok'):
                raise ValueError('rejected')
            return result['result']
        except Exception:
            raise RuntimeError('Telegram request failed') from None
    bridge = TelegramBridge(str(Path(runtime_dir) / 'forum.sqlite'), config['chat_id'], roles, call, None)
    worker = PollingWorker(bridge, store, scope, 'manager1')
    stop = threading.Event()
    bridge.db.execute('CREATE TABLE IF NOT EXISTS published(id TEXT PRIMARY KEY, state TEXT)')
    def run():
        while not stop.is_set():
            try:
                for role in ('manager1', 'manager2'):
                    for dialog in store.snapshot(role, scope)['dialogs']:
                        if dialog.get('status') != 'assigned' or dialog.get('assignment') != role:
                            continue
                        key = dialog['id']
                        notice = key + '/' + role
                        with bridge.lock:
                            if bridge.db.execute('SELECT 1 FROM published WHERE id=?', (notice,)).fetchone():
                                continue
                            bridge.db.execute('INSERT INTO published VALUES (?,?)', (notice, 'pending'))
                            bridge.db.commit()
                            # No original messages or personal data leave this installation.
                            reason = dialog['route']['reason']
                            manager_name = 'Менеджер 1' if role == 'manager1' else 'Менеджер 2'
                            bridge.publish(key, manager_name + '. ' + reason + '. Ответ можно отправить в этой теме.')
                            bridge.db.execute('UPDATE published SET state=? WHERE id=?', ('sent', notice))
                            bridge.db.commit()
                row = bridge.db.execute('SELECT offset FROM worker_cursor WHERE id=1').fetchone()
                for update in call('getUpdates', {'offset': row[0] if row else 0, 'timeout': 20, 'allowed_updates': ['message']}):
                    message = update.get('message', {})
                    role = roles.get(message.get('from', {}).get('id'))
                    if role:
                        bridge.deliver = lambda dialog, text, request, role=role: store.act(role, 'reply', {'dialog_id': dialog, 'text': text, 'request_id': request}, scope)
                        bridge.receive(update)
                    bridge.db.execute('INSERT OR REPLACE INTO worker_cursor VALUES (1,?)', (update['update_id'] + 1,))
                    bridge.db.commit()
            except Exception:
                stop.wait(5)
    thread = threading.Thread(target=run, daemon=True, name='support-forum')
    thread.start()
    return stop.set
