"""Administrator's durable run preference, separate from temporary recovery flags."""

import json
import uuid

from .config import atomic_json


class RunIntent:
    def __init__(self, data):
        self.path = data / "config/run-intent.json"
        # Existing installations keep their automatic startup until a manual choice.
        self.running = True
        self.revision = None
        self.error = None
        try:
            value = json.loads(self.path.read_text())
            if (
                not isinstance(value, dict)
                or type(value.get("version")) is not int
                or value.get("version") != 1
                or type(value.get("running")) is not bool
            ):
                raise ValueError
            revision = value.get("revision")
            if revision is not None and (
                not isinstance(revision, str)
                or len(revision) != 32
                or any(c not in "0123456789abcdef" for c in revision)
            ):
                raise ValueError
            self.running, self.revision = value["running"], revision
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.running = False
            self.error = "Preferência de execução inválida ou indisponível; jogo mantido parado. Use Iniciar ou Parar para gravar uma nova preferência."

    def set(self, running):
        revision = uuid.uuid4().hex
        try:
            atomic_json(self.path, {"version": 1, "running": running, "revision": revision})
        except OSError:
            self.error = "Não foi possível salvar a preferência de execução. Operação não aceita; confira espaço e permissões."
            raise ValueError(self.error) from None
        self.running, self.revision, self.error = running, revision, None
        return revision

    def describe(self):
        return {"running": self.running, "error": self.error}
