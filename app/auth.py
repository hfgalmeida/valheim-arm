import hashlib
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .config import atomic_json

hasher = PasswordHasher()


def set_password(data, password):
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Senha administrativa deve ter entre 12 e 256 caracteres")
    atomic_json(data / "config/admin.json", {"hash": hasher.hash(password)})


class Auth:
    def __init__(self, data):
        self.path = data / "config/admin.json"
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.dummy_hash = hasher.hash(secrets.token_urlsafe(32))

    def fingerprint(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else ""

    def login(self, password, address):
        now = time.monotonic()
        attempts = self.failures[address]
        while attempts and now - attempts[0] > 300:
            attempts.popleft()
        if len(attempts) >= 5:
            raise ValueError("Muitas tentativas; aguarde cinco minutos")
        attempts.append(now)
        import json

        fingerprint = self.fingerprint()
        credential = json.loads(self.path.read_text())["hash"] if fingerprint else self.dummy_hash
        try:
            valid = hasher.verify(credential, password)
        except VerificationError:
            valid = False
        if not valid or not fingerprint or fingerprint != self.fingerprint():
            raise ValueError("Credenciais inválidas")
        attempts.clear()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        if len(self.sessions) >= 32:
            self.sessions.pop(next(iter(self.sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[token] = {"csrf": csrf, "expires": now + 8 * 3600, "credential": fingerprint}
        return token, csrf

    def session(self, token):
        session = self.sessions.get(token or "")
        if (
            session
            and session["expires"] > time.monotonic()
            and session["credential"] == self.fingerprint()
        ):
            return session
        self.sessions.pop(token or "", None)
        return None
