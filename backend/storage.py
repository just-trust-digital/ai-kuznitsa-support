import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Storage:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workspace(
                    scope TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS offers(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    category TEXT, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_files(
                    scope TEXT NOT NULL, message_id TEXT NOT NULL, content BLOB NOT NULL,
                    PRIMARY KEY(scope, message_id)
                );
                CREATE TABLE IF NOT EXISTS usage(
                    scope TEXT, provider TEXT, calls INTEGER, metered_calls INTEGER,
                    input_tokens INTEGER, output_tokens INTEGER, PRIMARY KEY(scope, provider)
                );
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read(db, scope):
        row = db.execute("SELECT state FROM workspace WHERE scope=?", (scope,)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def save(db, scope, state):
        db.execute("INSERT OR REPLACE INTO workspace VALUES (?,?)",
                   (scope, json.dumps(state, ensure_ascii=False)))

    @staticmethod
    def add_usage(db, scope, provider, counters):
        db.execute("""
            INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, provider) DO UPDATE SET
                calls=calls+excluded.calls, metered_calls=metered_calls+excluded.metered_calls,
                input_tokens=input_tokens+excluded.input_tokens, output_tokens=output_tokens+excluded.output_tokens
        """, (scope, provider, *counters))
