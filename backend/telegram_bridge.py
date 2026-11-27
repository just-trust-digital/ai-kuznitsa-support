"""Forum bridge core. Bot API transport is injected; no default destination."""
import sqlite3
import threading


class TelegramBridge:
    def __init__(self, database, chat_id, operators, bot_call, deliver):
        if not chat_id or not operators:
            raise ValueError('Dedicated forum and authorized operators are required')
        self.chat_id, self.operators = int(chat_id), set(map(int, operators))
        self.call, self.deliver = bot_call, deliver
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.executescript('CREATE TABLE IF NOT EXISTS topics(dialog TEXT PRIMARY KEY, topic INTEGER UNIQUE); CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, state TEXT NOT NULL);')

    def publish(self, dialog_id, text):
        with self.lock:
            row = self.db.execute('SELECT topic FROM topics WHERE dialog=?', (dialog_id,)).fetchone()
            if not row:
                result = self.call('createForumTopic', {'chat_id': self.chat_id, 'name': 'Обращение ' + str(dialog_id)[:60]})
                topic = result['message_thread_id']
                self.db.execute('INSERT INTO topics VALUES (?,?)', (dialog_id, topic))
                self.db.commit()
            else:
                topic = row[0]
            return self.call('sendMessage', {'chat_id': self.chat_id, 'message_thread_id': topic, 'text': text})

    def receive(self, update):
        message = update.get('message', {})
        if message.get('chat', {}).get('id') != self.chat_id or message.get('from', {}).get('id') not in self.operators or message.get('from', {}).get('is_bot') or not message.get('text'):
            return False
        with self.lock:
            row = self.db.execute('SELECT dialog FROM topics WHERE topic=?', (message.get('message_thread_id'),)).fetchone()
            if not row:
                return False
            try:
                # Reserve durably before delivery. Ambiguous failures require review, never blind retry.
                self.db.execute('INSERT INTO updates VALUES (?,?)', (update['update_id'], 'pending'))
                self.db.commit()
            except sqlite3.IntegrityError:
                return False
            self.deliver(row[0], message['text'], 'telegram:' + str(update['update_id']))
            self.db.execute('UPDATE updates SET state=? WHERE id=?', ('delivered', update['update_id']))
            self.db.commit()
            return True

class PollingWorker:
    def __init__(self, bridge, store, scope, manager):
        if manager not in ('manager1', 'manager2'):
            raise ValueError('manager must be manager1 or manager2')
        self.bridge, self.store, self.scope, self.manager = bridge, store, scope, manager
        bridge.db.execute('CREATE TABLE IF NOT EXISTS worker_cursor(id INTEGER PRIMARY KEY, offset INTEGER)')
        bridge.deliver = lambda dialog, text, request: store.act(manager, 'reply', {'dialog_id': dialog, 'text': text, 'request_id': request}, scope)

    def poll_once(self):
        row = self.bridge.db.execute('SELECT offset FROM worker_cursor WHERE id=1').fetchone()
        updates = self.bridge.call('getUpdates', {'offset': row[0] if row else 0, 'timeout': 20, 'allowed_updates': ['message']})
        for update in updates:
            self.bridge.receive(update)
            self.bridge.db.execute('INSERT OR REPLACE INTO worker_cursor VALUES (1,?)', (update['update_id'] + 1,))
            self.bridge.db.commit()


def main():
    import os
    import json
    import time
    import urllib.request
    import argparse
    from .core import Store
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--bridge-database', required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--manager', choices=['manager1', 'manager2'], required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--operators', required=True, help='Comma-separated Telegram user IDs')
    args = parser.parse_args()
    token = os.environ.get('SUPPORT_TELEGRAM_BOT_TOKEN')
    if not token:
        parser.error('SUPPORT_TELEGRAM_BOT_TOKEN is required')
    def bot_call(method, payload):
        req = urllib.request.Request('https://api.telegram.org/bot' + token + '/' + method, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        try:
            from .integrations import NoRedirect
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                result = json.load(response)
            if not result.get('ok'):
                raise RuntimeError('Bot API rejected request')
            return result['result']
        except Exception:
            raise RuntimeError('Bot API request failed; details omitted') from None
    bridge = TelegramBridge(args.bridge_database, args.chat_id, args.operators.split(','), bot_call, None)
    worker = PollingWorker(bridge, Store(args.database), args.scope, args.manager)
    # Outbound publication is explicit: call bridge.publish for selected authorized dialogs.
    while True:
        try:
            worker.poll_once()
        except Exception:
            print('Telegram cycle failed; inspect pending delivery before retry', flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
