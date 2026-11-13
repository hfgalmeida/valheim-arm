"""Block a release unless Docker Hub confirms that its version tag is absent."""
import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def ensure_unpublished(image, version, opener=urllib.request.urlopen):
    if not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*", image):
        raise ValueError("Imagem inválida; use namespace/repositorio do Docker Hub")
    number = r"(?:0|[1-9][0-9]*)"
    if not re.fullmatch(rf"{number}\.{number}\.{number}", version):
        raise ValueError("Versão inválida; use X.Y.Z sem prefixo v")

    query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{image}:pull"})
    request = urllib.request.Request("https://auth.docker.io/token?" + query,
                                     headers={"User-Agent": "valheim-arm-release-check"})
    try:
        with opener(request, timeout=30) as response:
            body = json.load(response)
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError("Docker Hub não retornou um token de consulta válido")
        request = urllib.request.Request(
            f"https://registry-1.docker.io/v2/{image}/manifests/{version}",
            headers={"Authorization": "Bearer " + token, "Accept": MANIFEST_TYPES,
                     "User-Agent": "valheim-arm-release-check"},
        )
        try:
            with opener(request, timeout=30):
                pass
        except urllib.error.HTTPError as error:
            if error.code == 404:
                try:
                    body = json.load(error)
                    errors = body.get("errors") if isinstance(body, dict) else None
                    if isinstance(errors, list) and errors and all(
                            isinstance(item, dict) and item.get("code") == "MANIFEST_UNKNOWN" for item in errors):
                        return  # Only an explicit missing manifest permits publishing.
                except (ValueError, UnicodeError):
                    pass
            raise
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Consulta ao Docker Hub falhou (HTTP {error.code}); publicação bloqueada") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise RuntimeError("Não foi possível confirmar a ausência da tag no Docker Hub; publicação bloqueada") from error

    raise RuntimeError(f"A imagem {image}:{version} já está publicada. Aumente VERSION para publicar outra versão.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        ensure_unpublished(args.image, args.version)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    print(f"Tag {args.image}:{args.version} ainda não publicada; verificação aprovada.")


if __name__ == "__main__":
    main()
