import secrets
import time
import threading
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .config import atomic_json
from .accounts import Accounts

hasher = PasswordHasher()


def set_password(data, password):
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Administrator password must contain 12 to 256 characters")
    atomic_json(data / "config/admin.json", {"hash": hasher.hash(password)})


class Auth:
    def __init__(self, data):
        self.path = data / "config/admin.json"
        self.accounts = Accounts(data, hasher)
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.rate_lock = threading.Lock()
        self.dummy_hash = hasher.hash(secrets.token_urlsafe(32))

    def fingerprint(self, user_id="admin"):
        user = self.accounts.get(user_id)
        return self.accounts.fingerprint(user) if user else ""

    def attempt(self, key):
        with self.rate_lock:
            if len(self.failures) > 1024 and key not in self.failures:
                self.failures.pop(next(iter(self.failures)))
            now = time.monotonic()
            attempts = self.failures[key]
            while attempts and now - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 5:
                raise ValueError("Too many attempts; wait five minutes")
            attempts.append(now)
            return attempts

    def verify_current(self, user_id, password, expected):
        attempts = self.attempt(("password", user_id))
        user = self.accounts.get(user_id)
        if not user or self.accounts.fingerprint(user) != expected:
            return False
        try:
            valid = hasher.verify(user["hash"], password)
        except VerificationError:
            return False
        if valid:
            with self.rate_lock:
                attempts.clear()
        return valid

    def login(self, password, address, username="admin"):
        now = time.monotonic()
        attempts = self.attempt(("login", address))
        user = self.accounts.find(username)
        credential = user["hash"] if user else self.dummy_hash
        try:
            valid = hasher.verify(credential, password)
        except VerificationError:
            valid = False
        fingerprint = self.accounts.fingerprint(user) if user else ""
        if (
            not valid
            or not user
            or not user["enabled"]
            or fingerprint != self.fingerprint(user["id"])
        ):
            raise ValueError("Invalid credentials")
        with self.rate_lock:
            attempts.clear()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        if len(self.sessions) >= 128:
            self.sessions.pop(next(iter(self.sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[token] = {
            "csrf": csrf,
            "expires": now + 8 * 3600,
            "credential": fingerprint,
            "user": self.accounts.public(user),
        }
        return token, csrf

    def session(self, token):
        session = self.sessions.get(token or "")
        try:
            fingerprint = self.fingerprint(session["user"]["id"]) if session else ""
        except (OSError, ValueError, KeyError):
            fingerprint = ""
        if (
            session
            and session["expires"] > time.monotonic()
            and session["credential"] == fingerprint
        ):
            return session
        self.sessions.pop(token or "", None)
        return None
