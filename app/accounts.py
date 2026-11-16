"""Panel accounts; the original admin.json remains the recovery credential."""

import hashlib
import json
import re
import threading
import time
import uuid

from .config import atomic_json


class Accounts:
    def __init__(self, data, hasher):
        self.path = data / "config/panel-users.json"
        self.primary = data / "config/admin.json"
        self.hasher = hasher
        self.lock = threading.RLock()

    def records(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 1024**2:
            raise ValueError("Cadastro de contas excessivo")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, list)
            or len(value) > 50
            or any(
                not isinstance(u, dict)
                or not isinstance(u.get("id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", u["id"])
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", u.get("username", ""))
                or u.get("role") not in ("admin", "operator")
                or type(u.get("enabled")) is not bool
                or not isinstance(u.get("hash"), str)
                or type(u.get("must_change_password")) is not bool
                or u.get("username") == "admin"
                for u in value
            )
        ):
            raise ValueError("Cadastro de contas inválido; confira os arquivos no host")
        if len({u["id"] for u in value}) != len(value) or len(
            {u["username"] for u in value}
        ) != len(value):
            raise ValueError("Cadastro de contas duplicado")
        return value

    def all(self):
        with self.lock:
            users = self.records()
            if self.primary.exists():
                value = json.loads(self.primary.read_text(encoding="utf-8"))
                users = [
                    {
                        "id": "admin",
                        "username": "admin",
                        "role": "admin",
                        "enabled": True,
                        "must_change_password": bool(value.get("must_change_password", False)),
                        "hash": value["hash"],
                    }
                ] + users
            return users

    @staticmethod
    def public(user):
        return {k: user[k] for k in ("id", "username", "role", "enabled", "must_change_password")}

    @staticmethod
    def fingerprint(user):
        return hashlib.sha256(json.dumps(user, sort_keys=True).encode()).hexdigest()

    def get(self, user_id):
        return next((u for u in self.all() if u["id"] == user_id), None)

    def find(self, username):
        return next((u for u in self.all() if u["username"] == username.lower()), None)

    @staticmethod
    def validate_password(password):
        if not 12 <= len(password) <= 256:
            raise ValueError("A senha deve ter entre 12 e 256 caracteres")

    def create(self, username, role, password):
        username = username.lower().strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", username):
            raise ValueError(
                "Use de 3 a 32 letras sem acentos, números, ponto, hífen ou sublinhado"
            )
        if role not in ("admin", "operator"):
            raise ValueError("Perfil inválido")
        self.validate_password(password)
        with self.lock:
            users = self.records()
            if username == "admin" or any(u["username"] == username for u in users):
                raise ValueError("Nome de usuário já cadastrado")
            if len(users) >= 50:
                raise ValueError("Limite de 50 contas adicionais atingido")
            user = {
                "id": uuid.uuid4().hex,
                "username": username,
                "role": role,
                "enabled": True,
                "must_change_password": True,
                "hash": self.hasher.hash(password),
                "created": time.time(),
            }
            atomic_json(self.path, users + [user])
            return self.public(user)

    def change(self, user_id, role, enabled):
        if user_id == "admin":
            raise ValueError(
                "A conta admin principal é preservada para recuperação e não pode ser desativada ou mudar de perfil"
            )
        if role not in ("admin", "operator") or type(enabled) is not bool:
            raise ValueError("Perfil ou estado inválido")
        with self.lock:
            users = self.records()
            user = next((u for u in users if u["id"] == user_id), None)
            if user is None:
                raise ValueError("Conta não encontrada")
            user.update(role=role, enabled=enabled, revision=uuid.uuid4().hex)
            atomic_json(self.path, users)
            return self.public(user)

    def password(self, user_id, password, temporary=False, expected=None):
        self.validate_password(password)
        with self.lock:
            user = self.get(user_id)
            if not user:
                raise ValueError("Conta não encontrada")
            if expected is not None and self.fingerprint(user) != expected:
                raise ValueError("Credenciais alteradas; entre novamente")
            hashed = self.hasher.hash(password)
            if user_id == "admin":
                atomic_json(self.primary, {"hash": hashed, "must_change_password": temporary})
            else:
                users = self.records()
                for item in users:
                    if item["id"] == user_id:
                        item.update(
                            hash=hashed, must_change_password=temporary, revision=uuid.uuid4().hex
                        )
                atomic_json(self.path, users)

    def allowed_actor(self, actor):
        user = self.get(actor["id"])
        return bool(
            user
            and user["enabled"]
            and not user.get("must_change_password")
            and self.fingerprint(user) == actor.get("credential")
        )
