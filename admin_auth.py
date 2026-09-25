"""
Login, CSRF protection, brute-force lockout, security headers and audit log
for the admin panel (admin_app.py).

Configuration (environment):
  ADMIN_USERNAME        login name (default "admin")
  ADMIN_PASSWORD_HASH   werkzeug password hash - generate with:
                          python admin_auth.py hash
  ADMIN_SECRET_KEY      random key (>= 32 chars) signing the session cookie
  ADMIN_COOKIE_SECURE   "true" when the panel is served over HTTPS
                        (default "false": SSH tunnel to http://127.0.0.1)

Without ADMIN_PASSWORD_HASH and ADMIN_SECRET_KEY the panel refuses every
request (fail closed) instead of running unprotected.
"""
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from html import escape

from flask import jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from shared_utils import get_logger

logger = get_logger('AdminAuth')

SESSION_HOURS = 8
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_PATHS = {"/login", "/healthz"}

CDN_CSS = ("https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
           "sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH")

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'; object-src 'none'")


class _FailedLogins:
    """Failed attempts per client address; locks the address out after MAX_FAILURES."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}  # key -> (failures, first_failure_ts, locked_until_ts)

    def locked_for(self, key):
        with self._lock:
            _, _, until = self._state.get(key, (0, 0, 0))
            return max(0, int(until - time.time()))

    def failure(self, key):
        now = time.time()
        with self._lock:
            count, first, _ = self._state.get(key, (0, now, 0))
            if now - first > LOCKOUT_SECONDS:
                count, first = 0, now
            count += 1
            until = now + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0
            self._state[key] = (count, first, until)
            return count, until > 0

    def success(self, key):
        with self._lock:
            self._state.pop(key, None)


failed_logins = _FailedLogins()


def _configured():
    return bool(os.getenv("ADMIN_PASSWORD_HASH")) and len(os.getenv("ADMIN_SECRET_KEY", "")) >= 32


def _client_key():
    return request.remote_addr or "unknown"


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_ok():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("csrf", "")
    return bool(expected) and hmac.compare_digest(sent, expected)


def _audit(db, action, **extra):
    entry = {"at": datetime.now(), "user": session.get("admin"), "ip": _client_key(), "action": action, **extra}
    logger.info(f"Admin audit: {action} {extra}")
    try:
        db.admin_audit.insert_one(entry)
    except Exception as e:  # the audit log must never block the panel
        logger.error(f"Could not write admin audit entry: {e}")


def _login_page(error="", status=200):
    href, sri = CDN_CSS
    msg = f'<div class="alert alert-danger py-2">{escape(error)}</div>' if error else ""
    html = f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Panel administracyjny – logowanie</title>
<link rel="stylesheet" href="{href}" integrity="{sri}" crossorigin="anonymous"></head>
<body class="bg-light"><main class="container" style="max-width:380px;margin-top:12vh">
<h1 class="h4 mb-3">Panel administracyjny</h1>{msg}
<form method="post" action="/login" autocomplete="off">
<input type="hidden" name="csrf_token" value="{escape(csrf_token())}">
<div class="mb-2"><label class="form-label" for="u">Login</label>
<input class="form-control" id="u" name="username" required autofocus></div>
<div class="mb-3"><label class="form-label" for="p">Hasło</label>
<input class="form-control" id="p" name="password" type="password" required></div>
<button class="btn btn-primary w-100" type="submit">Zaloguj</button></form></main></body></html>"""
    return html, status, {"Content-Type": "text/html; charset=utf-8"}


def init_admin_auth(app, db):
    app.config.update(
        SECRET_KEY=os.getenv("ADMIN_SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_NAME="wspa_admin",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if not _configured():
        logger.error("ADMIN_PASSWORD_HASH / ADMIN_SECRET_KEY (>= 32 chars) not set - admin panel is locked")

    @app.before_request
    def require_login():
        if request.path == "/healthz":
            return None
        if not _configured():
            return jsonify({"error": "Admin panel credentials are not configured"}), 503
        if request.method in UNSAFE_METHODS and not _csrf_ok():
            _audit(db, "csrf_rejected", path=request.path, method=request.method)
            return jsonify({"error": "Invalid CSRF token"}), 403
        if request.path in PUBLIC_PATHS or session.get("admin"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect("/login")

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/") or request.path == "/login":
            response.headers["Cache-Control"] = "no-store"
        if request.method in UNSAFE_METHODS and session.get("admin") and request.path.startswith("/api/"):
            _audit(db, "api_call", method=request.method, path=request.path, status=response.status_code)
        return response

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.route("/login", methods=["GET"])
    def login_form():
        if session.get("admin"):
            return redirect("/")
        return _login_page()

    @app.route("/login", methods=["POST"])
    def login():
        key = _client_key()
        wait = failed_logins.locked_for(key)
        if wait:
            return _login_page(f"Zbyt wiele nieudanych prób. Spróbuj ponownie za {wait // 60 + 1} min.", 429)
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        expected_user = os.getenv("ADMIN_USERNAME", "admin")
        user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
        # Always run the hash check so timing does not reveal a valid login name
        password_ok = check_password_hash(os.getenv("ADMIN_PASSWORD_HASH", ""), password)
        if not (user_ok and password_ok):
            count, locked = failed_logins.failure(key)
            _audit(db, "login_failed", username=username[:64], attempt=count, locked=locked)
            return _login_page("Nieprawidłowy login lub hasło.", 401)
        failed_logins.success(key)
        session.clear()  # new session id on login (session fixation)
        session.permanent = True
        session["admin"] = expected_user
        csrf_token()
        _audit(db, "login")
        return redirect("/")

    @app.route("/logout", methods=["POST"])
    def logout():
        _audit(db, "logout")
        session.clear()
        return redirect("/login")

    @app.route("/api/session")
    def session_info():
        return jsonify({"user": session.get("admin")})


if __name__ == "__main__":
    import getpass
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "hash":
        pw = getpass.getpass("Hasło administratora: ")
        if len(pw) < 12:
            sys.exit("Hasło musi mieć co najmniej 12 znaków.")
        if pw != getpass.getpass("Powtórz hasło: "):
            sys.exit("Hasła się różnią.")
        # Ready to paste into backend/.env. The single quotes matter: the hash
        # contains "$", which Docker Compose would otherwise expand in env_file
        print("Wklej do backend/.env:")
        print(f"ADMIN_PASSWORD_HASH='{generate_password_hash(pw)}'")
        print(f"ADMIN_SECRET_KEY={secrets.token_hex(32)}")
    else:
        print("Użycie: python admin_auth.py hash")
