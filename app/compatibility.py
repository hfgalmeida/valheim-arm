"""Read pre-1.0 operation records without changing user-provided names or settings."""

LEGACY_STATES = {
    "fila": "queued",
    "executando": "running",
    "adiado": "deferred",
    "cancelado": "cancelled",
    "conclu\u00eddo": "completed",
    "falha": "failed",
    "solicitado": "requested",
    "negado": "denied",
    "revertido": "rolled_back",
    "interrompido": "interrupted",
}
LEGACY_FIELDS = {
    "antes": "before",
    "depois": "after",
    "alterado": "changed",
    "ativado": "enabled",
    "arquivo": "file",
    "nome": "name",
    "origem": "source",
    "plataforma": "platform",
    "conex\u00e3o": "connection",
    "c\u00f3pia": "copy",
    "revis\u00e3o": "revision",
    "entradas": "entries",
    "conta": "account",
    "senha": "password",
}


def operation_record(record):
    result = dict(record)
    for key in ("state", "outcome"):
        if key in result:
            result[key] = LEGACY_STATES.get(result[key], result[key])
    if isinstance(result.get("actor"), dict) and result["actor"].get("id") == "system":
        result["actor"] = result["actor"] | {"username": "system"}
    if isinstance(result.get("changes"), dict):
        result["changes"] = change_fields(result["changes"])
    return result


def change_fields(value):
    if isinstance(value, dict):
        return {LEGACY_FIELDS.get(key, key): change_fields(item) for key, item in value.items()}
    # Values may be user names, passwords-redacted text, or plugin settings.
    return value
