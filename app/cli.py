import argparse
import getpass
import logging
import os
import sys
import urllib.request
from pathlib import Path

from .auth import set_password


def main():
    parser = argparse.ArgumentParser(description="Valheim ARM — supervisor e administração local")
    parser.add_argument("command", choices=("serve", "set-password", "health", "preflight"))
    args = parser.parse_args()
    data = Path(os.environ.get("DATA_DIR", "/data"))
    os.umask(0o077)
    if args.command == "set-password":
        password = getpass.getpass("Nova senha administrativa (mínimo 12 caracteres): ")
        if password != getpass.getpass("Repita a senha: "):
            raise SystemExit("As senhas não conferem")
        set_password(data, password)
        print("Senha atualizada; sessões anteriores foram invalidadas.")
    elif args.command == "health":
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=3) as result:
                sys.exit(0 if result.status == 200 else 1)
        except OSError:
            sys.exit(1)
    elif args.command == "preflight":
        from .runtime import preflight
        preflight(data)
        print("Preflight ARM/FEX aprovado.")
    else:
        import uvicorn
        from .web import create_app
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        # One worker owns the game process and in-memory sessions/queue.
        # Bound HTTP/SSE drain time separately so lifespan shutdown retains
        # enough of Docker's six-minute grace period to save the world.
        uvicorn.run(create_app(data), host="0.0.0.0", port=8080, workers=1, proxy_headers=False,
                    timeout_graceful_shutdown=10, access_log=False)


if __name__ == "__main__":
    main()
