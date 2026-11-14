"""Published public-image version, checked outside HTTP requests."""

import asyncio
import json
from pathlib import Path
import re
import time
import urllib.request

REPOSITORY = "hfgalmeida/valheim-arm"


def arm_digest(tag):
    return next(
        (
            image.get("digest")
            for image in tag.get("images", [])
            if image.get("architecture") == "arm64" and image.get("os") == "linux"
        ),
        None,
    )


def published_version():
    base = (
        "https://hub.docker.com/v2/namespaces/"
        + REPOSITORY.split("/")[0]
        + "/repositories/"
        + REPOSITORY.split("/")[1]
        + "/tags"
    )

    def read(url):
        request = urllib.request.Request(url, headers={"User-Agent": "ValheimARM/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    digest = arm_digest(read(base + "/latest"))
    if not digest:
        raise ValueError
    tags = read(base + "?page_size=100&ordering=last_updated")
    matches = [
        tag["name"]
        for tag in tags["results"]
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", tag["name"]) and arm_digest(tag) == digest
    ]
    if not matches:
        raise ValueError
    return max(matches, key=lambda value: tuple(map(int, value.split("."))))


class ImageVersion:
    def __init__(self):
        version_file = Path(__file__).resolve().parents[1] / "VERSION"
        self.installed = version_file.read_text().strip() if version_file.exists() else None
        self.available = None
        self.checked = None
        self.error = None

    def describe(self):
        newer = None
        if self.installed and self.available:
            try:
                newer = tuple(map(int, self.available.split("."))) > tuple(
                    map(int, self.installed.split("."))
                )
            except ValueError:
                pass
        return {
            "installed": self.installed,
            "available": self.available,
            "update_available": newer,
            "checked": self.checked,
            "error": self.error,
            "repository": REPOSITORY,
        }

    async def run(self):
        while True:
            try:
                self.available = await asyncio.to_thread(published_version)
                self.checked, self.error = time.time(), None
            except Exception:
                self.error = "Não foi possível consultar a imagem pública no Docker Hub."
            await asyncio.sleep(6 * 3600)
