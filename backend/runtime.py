"""Runtime wiring; all network work happens outside Store transactions."""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from .core import Store
from .integrations import IntegrationEngine, HybridEngine
from .telegram_bridge import TelegramBridge
from .telegram_notifications import ManagerNotifications


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
            os.environ.get('SUPPORT_CLOUD_API_KEY'), cloud_generator=generate_public if provider == 'codex' else None,
            clef_account=os.environ['CLOUDFLARE_ACCOUNT_ID'] if hybrid.get('clef') else None,
            clef_token=os.environ.get('CLOUDFLARE_API_TOKEN'))
    store = Store(db, feed, corpus, engine=engine, hybrid_engine=hybrid_engine)
    store.business_hours = lambda: 9 <= (datetime.now(timezone.utc).hour + 3) % 24 < 19
    store.runtime_config = config
    return store


def start_telegram(store, runtime_dir):
    config = getattr(store, 'runtime_config', {}).get('telegram', {})
    token = os.environ.get('SUPPORT_TELEGRAM_BOT_TOKEN')
    if not token:
        return lambda: None
    forum = config.get('chat_id')
    scope = config.get('scope', '')
    if forum and (not scope.startswith('jury') or config.get('synthetic_only') is not True):
        raise ValueError('Telegram requires an explicitly synthetic jury workspace')
    roles = {int(k): v for k, v in config.get('operators', {}).items()}
    if forum and (not roles or any(v not in ('manager1', 'manager2') for v in roles.values())):
        raise ValueError('Telegram user-to-manager mapping is required')
    from .integrations import post, windows_https_proxy
    proxy = None
    if os.name == 'nt':
        try:
            proxy = windows_https_proxy('Telegram')
        except (OSError, RuntimeError):
            pass

    def call(method, payload):
        try:
            result = post('https://api.telegram.org/bot' + token + '/' + method, payload, proxy=proxy)
            if not result.get('ok'):
                raise ValueError('rejected')
            return result['result']
        except Exception:
            raise RuntimeError('Telegram request failed') from None
    bridge = TelegramBridge(str(Path(runtime_dir) / 'forum.sqlite'), forum, roles, call, None) if forum else None
    notifications = ManagerNotifications(store.storage, call)
    store.manager_notifications = notifications
    stop = threading.Event()
    if bridge:
        bridge.db.execute('CREATE TABLE IF NOT EXISTS worker_cursor(id INTEGER PRIMARY KEY, offset INTEGER)')
        bridge.db.execute('CREATE TABLE IF NOT EXISTS published(id TEXT PRIMARY KEY, state TEXT)')

    def publish_forum():
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
                    reason = dialog['route']['reason']
                    manager_name = 'Менеджер 1' if role == 'manager1' else 'Менеджер 2'
                    bridge.publish(key, manager_name + '. ' + reason + '. Ответ можно отправить в этой теме.')
                    bridge.db.execute('UPDATE published SET state=? WHERE id=?', ('sent', notice))
                    bridge.db.commit()

    def run():
        private_bot = None
        reported_failure = False
        while not stop.is_set():
            try:
                if private_bot is None:
                    is_test_bot = call('getMe', {}).get('username', '').lower() == 'jtd_test_bot'
                    if is_test_bot and call('getWebhookInfo', {}).get('url'):
                        print('Telegram polling requires a bot without an active webhook', flush=True)
                        return
                    private_bot = is_test_bot
                    if not private_bot and not bridge:
                        print('Telegram notifications require @jtd_test_bot', flush=True)
                        return
                offset = notifications.offset()
                if bridge:
                    row = bridge.db.execute('SELECT offset FROM worker_cursor WHERE id=1').fetchone()
                    offset = max(offset, row[0] if row else 0)
                updates = call('getUpdates', {'offset': offset, 'timeout': 3 if private_bot else 20,
                                              'allowed_updates': ['message']})
                notifications.ready = private_bot
                for update in updates:
                    if stop.is_set():
                        return
                    message = update.get('message', {})
                    role = roles.get(message.get('from', {}).get('id'))
                    if private_bot:
                        notifications.receive(message, update['update_id'])
                    if bridge and role:
                        bridge.deliver = lambda dialog, text, request, role=role: store.act(role, 'reply', {'dialog_id': dialog, 'text': text, 'request_id': request}, scope)
                        bridge.receive(update)
                    if bridge:
                        bridge.db.execute('INSERT OR REPLACE INTO worker_cursor VALUES (1,?)', (update['update_id'] + 1,))
                        bridge.db.commit()
                    if private_bot:
                        notifications.offset(update['update_id'] + 1)
                if private_bot:
                    notifications.flush()
                if bridge:
                    publish_forum()
                reported_failure = False
            except Exception as exc:
                notifications.ready = False
                if not reported_failure:
                    print(f'Telegram worker unavailable: {type(exc).__name__}', flush=True)
                    reported_failure = True
                stop.wait(5)
    thread = threading.Thread(target=run, daemon=True, name='support-telegram')
    thread.start()
    return stop.set
