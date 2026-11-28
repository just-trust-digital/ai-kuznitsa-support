"""One-time pairing and assignment notifications for a manager's stand session."""
import hashlib
import re
import secrets
import sys
import time
from datetime import datetime, timezone


MANAGER_ROLES = ("manager1", "manager2")
CODE_LIFETIME = 300
SESSION_LIFETIME = 12 * 3600


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class ManagerNotifications:
    def __init__(self, storage, call):
        self.storage, self.call = storage, call
        self.ready = False
        with storage.connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_codes(
                chat INTEGER PRIMARY KEY, code TEXT UNIQUE, issued INTEGER, expires INTEGER)""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_sessions(
                session TEXT, scope TEXT, role TEXT, chat INTEGER, expires INTEGER,
                PRIMARY KEY(session, scope, role))""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_attempts(
                scope TEXT, role TEXT, bucket INTEGER, count INTEGER,
                PRIMARY KEY(scope, role, bucket))""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_outbox(
                id INTEGER PRIMARY KEY, scope TEXT, role TEXT, chat INTEGER, expires INTEGER)""")
            db.execute("CREATE TABLE IF NOT EXISTS telegram_cursor(id INTEGER PRIMARY KEY, offset INTEGER)")

    def session_action(self, session, scope, role, action, code=None):
        now = int(time.time())
        session = digest(session)
        with self.storage.connect(write=action != "status") as db:
            if action == "logout":
                db.execute("DELETE FROM telegram_sessions WHERE session=? AND scope=?", (session, scope))
                return {}
            key = (session, scope, role)
            if action == "disconnect":
                db.execute("DELETE FROM telegram_sessions WHERE session=? AND scope=? AND role=?", key)
            elif action == "connect":
                if not self.ready:
                    return {"error": "Бот сейчас недоступен. Повторите позже."}
                if not isinstance(code, str) or not re.fullmatch(r"[0-9]{10}", code):
                    return {"error": "Введите 10 цифр из сообщения бота."}
                bucket = now // 60
                db.execute("DELETE FROM telegram_attempts WHERE bucket<?", (bucket,))
                attempts = db.execute("""INSERT INTO telegram_attempts VALUES (?, ?, ?, 1)
                    ON CONFLICT(scope, role, bucket) DO UPDATE SET count=count+1 RETURNING count""",
                    (scope, role, bucket)).fetchone()[0]
                if attempts > 30:
                    return {"error": "Слишком много попыток. Повторите через минуту."}
                match = db.execute("DELETE FROM telegram_codes WHERE code=? AND expires>? RETURNING chat",
                                   (digest(code), now)).fetchone()
                if not match:
                    return {"error": "Код истёк, уже использован или введён с ошибкой. Отправьте боту /start."}
                db.execute("DELETE FROM telegram_sessions WHERE expires<=?", (now,))
                db.execute("INSERT OR REPLACE INTO telegram_sessions VALUES (?, ?, ?, ?, ?)",
                           (*key, match[0], now + SESSION_LIFETIME))
            row = db.execute("""SELECT expires FROM telegram_sessions
                WHERE session=? AND scope=? AND role=? AND expires>?""", (*key, now)).fetchone()
            return {"available": self.ready, "expires": row[0] if row else None}

    def receive(self, message, update_id):
        chat = message.get("chat", {})
        sender = message.get("from", {})
        command = (message.get("text") or "").split(maxsplit=1)
        now = int(time.time())
        if (chat.get("type") != "private" or sender.get("is_bot") or sender.get("id") != chat.get("id")
                or not isinstance(chat.get("id"), int) or not command
                or command[0].lower() not in ("/start", "/start@jtd_test_bot")
                or message.get("date", 0) <= now - CODE_LIFETIME):
            return
        with self.storage.connect(write=True) as db:
            db.execute("DELETE FROM telegram_codes WHERE expires<=?", (now,))
            current = db.execute("SELECT issued FROM telegram_codes WHERE chat=?", (chat["id"],)).fetchone()
            if current and now - current[0] < 5:
                return
            while True:
                code = f"{secrets.randbelow(10**10):010}"
                hashed = digest(code)
                if not db.execute("SELECT 1 FROM telegram_codes WHERE code=?", (hashed,)).fetchone():
                    break
            db.execute("INSERT OR REPLACE INTO telegram_codes VALUES (?, ?, ?, ?)",
                       (chat["id"], hashed, now, now + CODE_LIFETIME))
        try:
            self.call("sendMessage", {"chat_id": chat["id"], "protect_content": True,
                      "text": f"Код для стенда Softline\n\n{code}\n\nВведите его на стенде в разделе «Уведомления в Telegram». Код действует 5 минут."})
        except RuntimeError:
            print(f"Telegram pairing response failed (update {update_id})", file=sys.stderr, flush=True)

    @staticmethod
    def assignments(state):
        return {(entry["id"], entry["assignment"]) for entry in state["dialogs"].values()
                if entry["status"] == "assigned" and entry["assignment"] in MANAGER_ROLES}

    def enqueue(self, db, scope, roles):
        now = int(time.time())
        for role in roles:
            db.execute("""INSERT INTO telegram_outbox(scope, role, chat, expires)
                SELECT scope, role, chat, ? FROM telegram_sessions
                WHERE scope=? AND role=? AND expires>? GROUP BY scope, role, chat""",
                (now + CODE_LIFETIME, scope, role, now))

    def flush(self):
        now = int(time.time())
        with self.storage.connect(write=True) as db:
            db.execute("DELETE FROM telegram_sessions WHERE expires<=?", (now,))
            db.execute("DELETE FROM telegram_codes WHERE expires<=?", (now,))
            db.execute("DELETE FROM telegram_outbox WHERE expires<=?", (now,))
            items = db.execute("""SELECT id FROM telegram_outbox
                ORDER BY id LIMIT 20""").fetchall()
        for (item,) in items:
            with self.storage.connect(write=True) as db:
                notice = db.execute("DELETE FROM telegram_outbox WHERE id=? RETURNING scope, role, chat",
                                    (item,)).fetchone()
                if not notice:
                    continue
                scope, role, chat = notice
                active = db.execute("""SELECT 1 FROM telegram_sessions
                    WHERE scope=? AND role=? AND chat=? AND expires>?""",
                    (scope, role, chat, int(time.time()))).fetchone()
            if active:
                manager = "Менеджер 1" if role == "manager1" else "Менеджер 2"
                try:
                    self.call("sendMessage", {"chat_id": chat,
                              "text": f"Softline · {manager}\nВам назначено новое обращение. Откройте очередь чатов на стенде."})
                except RuntimeError:
                    print(f"Telegram assignment notification failed (item {item})", file=sys.stderr, flush=True)

    def offset(self, new=None):
        with self.storage.connect(write=new is not None) as db:
            if new is not None:
                db.execute("INSERT OR REPLACE INTO telegram_cursor VALUES (1, ?)", (new,))
                return
            row = db.execute("SELECT offset FROM telegram_cursor WHERE id=1").fetchone()
            return row[0] if row else 0
