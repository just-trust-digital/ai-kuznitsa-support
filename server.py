"""Local support stand. Serve public PWA assets and bearer-protected application data."""
import argparse
import hmac
import json
import mimetypes
import os
import re
import socket
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from backend.runtime import build_store, start_telegram
from backend.telegram_notifications import MANAGER_ROLES
from backend.customer import guest_id, request_customer
from backend.documents import PROFILES

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
ROLES = ("buyer", "manager1", "manager2", "jury", "developer")
JURY_ROLES = ("buyer", "manager1", "manager2", "guest")


def access_file(directory):
    credentials = credential_group(directory, "access.json", ROLES)
    credentials.update(credential_group(directory, "personal-access.json", ("personal",)))
    return credentials


def credential_group(directory, filename, roles):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / filename
    if not path.exists():
        candidate = directory / (".access-" + secrets.token_hex(8))
        with os.fdopen(os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            json.dump({role: secrets.token_urlsafe(32) for role in roles}, output, indent=2)
        try:
            if os.name == "nt":
                # Windows rename is atomic and refuses to replace an existing destination.
                os.rename(candidate, path)
            else:
                os.link(candidate, path)
        except FileExistsError:
            pass
        finally:
            candidate.unlink(missing_ok=True)
    credentials = json.loads(path.read_text(encoding="utf-8"))
    if (set(credentials) != set(roles)
            or not all(isinstance(t, str) and t.isascii() and len(t) >= 32 for t in credentials.values())
            or len(set(credentials.values())) != len(roles)):
        raise ValueError("Invalid private/access.json")
    return credentials


class SupportServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def buyer_view(result):
    """Remove staff annotations and unpublished catalog prices at the response boundary."""
    def public_offers(offers):
        for offer in offers:
            if not offer.get("available"):
                offer["price"] = None

    public_offers(result.get("offers", ()))
    public_offers(result.get("catalog", ()))
    dialogs = list(result.get("dialogs", ()))
    if "dialog" in result:
        dialogs.append(result["dialog"])
    for dialog in dialogs:
        for key in ("note", "flags", "label"):
            dialog.pop(key, None)
        for message in dialog.get("messages", ()):
            if isinstance(message.get("model"), dict):
                message["model"].pop("draft", None)
            public_offers(message.get("offers", ()))
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "Support"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        # Never write query strings, message bodies, or authentication data.
        pass

    def send(self, status, data, content_type, body=True):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def json_response(self, status, data):
        self.send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")

    def identity(self):
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ")
        if header != token and token.isascii():
            if guest_id(token):
                return "guest"
            for role, expected in self.server.credentials.items():
                if hmac.compare_digest(token, expected):
                    return role
        return None

    def api(self, method, url):
        identity = self.identity()
        if not identity:
            return self.json_response(401, {"error": "Введите действующий токен доступа."})
        roles = JURY_ROLES if identity == "jury" else ("buyer",) if identity == "personal" else (identity,)
        scope = "jury-demo" if identity == "jury" else "stand"
        if method == "GET" and url.path == "/api/me":
            return self.json_response(200, {"role": identity, "roles": roles})
        subject = guest_id(self.headers.get("Authorization", "")[7:]) if identity == "guest" else (
            identity if identity == "personal" else "buyer1" if identity == "buyer" else None)
        jury_visitor = guest_id(self.headers.get("X-Stand-Guest")) if identity == "jury" else None
        notifications = getattr(self.server.store, "manager_notifications", None)
        browser_session = self.headers.get("X-Stand-Session", "")
        valid_session = re.fullmatch(r"[0-9a-f-]{36}", browser_session) is not None
        context = request_customer.set(subject)
        try:
            if method == "GET" and url.path in {"/api/state", "/api/chat"}:
                params = parse_qs(url.query)
                role = params.get("role", [roles[0]])[0]
                if role not in roles:
                    raise PermissionError()
                guest_view = role == "guest"
                if guest_view:
                    if identity == "jury":
                        if not jury_visitor:
                            raise PermissionError()
                        request_customer.set(jury_visitor)
                    role = "buyer"
                result = (self.server.store.chat_snapshot(role, scope, params.get("id", [""])[0])
                          if url.path == "/api/chat" else self.server.store.snapshot(role, scope))
                if guest_view and url.path == "/api/state":
                    result["role"] = "guest"
                    result.pop("handoff_stats", None)
                elif url.path == "/api/state" and role == "buyer":
                    buyer = result["buyer_id"]
                    result["customer"] = ({"type": "individual", "full_name": PROFILES[buyer]}
                                          if buyer == "personal" else {"type": "company", "company_name": PROFILES[buyer]})
                    if identity == "jury":
                        result["client_profiles"] = PROFILES
                if url.path == "/api/state" and role in MANAGER_ROLES and notifications and valid_session:
                    result["telegram"] = notifications.session_action(browser_session, scope, role, "status")
            elif method == "POST" and url.path == "/api/action":
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1_450_000:
                    return self.json_response(413, {"error": "Недопустимый размер запроса."})
                if self.headers.get_content_type() != "application/json":
                    return self.json_response(415, {"error": "Требуется JSON."})
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError()
                role, action, payload = value.get("role", roles[0]), value.get("action"), value.get("payload", {})
                if role not in roles:
                    raise PermissionError()
                if not isinstance(action, str) or not isinstance(payload, dict):
                    raise ValueError()
                if action in {"telegram_connect", "telegram_disconnect", "telegram_logout"}:
                    operation = action.removeprefix("telegram_")
                    if not valid_session or (role not in MANAGER_ROLES and not
                            (operation == "logout" and identity == "jury")):
                        raise PermissionError()
                    if not notifications:
                        return self.json_response(503, {"error": "Уведомления в Telegram недоступны."})
                    result = notifications.session_action(browser_session, scope, role, operation, payload.get("code"))
                    return self.json_response(400 if "error" in result else 200, result)
                if action == "continue_guest_chat":
                    visitor = guest_id(payload.get("guest_token"))
                    if identity not in {"buyer", "personal"} or not visitor:
                        raise PermissionError()
                    self.server.store.continue_guest_chat(scope, visitor, str(payload.get("dialog_id", "")))
                    return self.json_response(200, {})
                if role == "guest":
                    if action not in {"message", "search", "end_chat", "handoff", "price_request", "chat_file"}:
                        raise PermissionError()
                    if identity == "jury":
                        if not jury_visitor:
                            raise PermissionError()
                        request_customer.set(jury_visitor)
                    role = "buyer"
                    if action == "message":
                        payload["mode"] = "rules"
                if action in {"buyer_select", "clear_history"} and identity != "jury":
                    raise PermissionError()
                if length > 65536 and (action not in {"message", "reply"} or not payload.get("attachment")):
                    return self.json_response(413, {"error": "Недопустимый размер запроса."})
                if action == "feed_import" and identity not in ("developer", "jury"):
                    raise PermissionError()
                if action == "feed_import" and identity == "jury":
                    role = "developer"
                    payload = {"day": int(payload["day"])}
                result = self.server.store.act(role, action, payload, scope)
            else:
                return self.json_response(404, {"error": "Маршрут не найден."})
            self.json_response(200, buyer_view(result) if role == "buyer" else result)
        except PermissionError:
            self.json_response(403, {"error": "Для этой роли действие недоступно."})
        except KeyError:
            self.json_response(404, {"error": "Запись не найдена."})
        except (ValueError, TypeError, OverflowError):
            self.json_response(400, {"error": "Проверьте поля запроса."})
        except Exception as exc:
            print(f"Support request failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            self.json_response(500, {"error": "Не удалось выполнить запрос."})
        finally:
            request_customer.reset(context)

    def do_POST(self):
        self.api("POST", urlsplit(self.path))

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            return self.api("GET", url)
        name = unquote(url.path).lstrip("/") or "index.html"
        path = (WEB / name).resolve()
        status = 200
        if not path.is_relative_to(WEB) or not path.is_file():
            path = WEB / "404.html"
            status = 404
        if not path.is_file():
            return self.send(404, b"Not found", "text/plain")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            mime = "text/javascript"
        if path.suffix == ".webmanifest":
            mime = "application/manifest+json"
        self.send(status, path.read_bytes(), mime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="Listen address. Use a private interface behind a reverse proxy.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--runtime", type=Path, default=ROOT / "private" / "runtime")
    parser.add_argument("--access-dir", type=Path, default=os.environ.get("SUPPORT_ACCESS_DIR") or ROOT / "private" / "runtime",
                        help="Directory for stable access tokens, shared by local previews by default.")
    parser.add_argument("--materials", type=Path, default=ROOT / "private" / "support-agent")
    parser.add_argument("--init-access", action="store_true")
    args = parser.parse_args()
    credentials = access_file(args.access_dir)
    if args.init_access:
        return
    feed = args.materials / "Mindbox.yml.gz.xml"
    corpus = args.materials / "support-chats.zip"
    with SupportServer((args.bind, args.port), Handler) as server:
        server.credentials = credentials
        server.store = build_store(args.runtime / "stand.sqlite3", feed if feed.is_file() else None,
                                   corpus if corpus.is_file() else None, args.runtime)
        stop_telegram = start_telegram(server.store, args.runtime)
        print(f"Support stand: http://{args.bind}:{args.port}/?utm_source=jtd_dev", flush=True)
        try:
            server.serve_forever()
        finally:
            stop_telegram()


if __name__ == "__main__":
    main()
