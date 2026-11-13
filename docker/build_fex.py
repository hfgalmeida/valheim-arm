"""Native-only FEX build. Resolve latest once, record immutable provenance."""
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

version, jobs = sys.argv[1:]
if version == "latest":
    request = urllib.request.Request("https://api.github.com/repos/FEX-Emu/FEX/releases/latest", headers={"User-Agent": "valheim-arm-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        version = json.load(response)["tag_name"]
if not re.fullmatch(r"FEX-\d{4}(?:\.\d+)?", version):
    raise SystemExit("FEX_VERSION deve ser latest ou uma tag estável FEX-AAAA[.N]")
if not jobs.isdigit() or not 1 <= int(jobs) <= 8:
    raise SystemExit("BUILD_JOBS deve estar entre 1 e 8")
subprocess.run(["git", "clone", "--recursive", "--depth", "1", "--branch", version,
                "https://github.com/FEX-Emu/FEX.git", "/tmp/FEX"], check=True)
commit = subprocess.check_output(["git", "-C", "/tmp/FEX", "rev-parse", "HEAD"], text=True).strip()
subprocess.run(["cmake", "-S", "/tmp/FEX", "-B", "/tmp/build", "-G", "Ninja",
                "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=clang-18", "-DCMAKE_CXX_COMPILER=clang++-18",
                "-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-18",
                "-DCMAKE_INSTALL_PREFIX=/usr/local", "-DBUILD_TESTING=OFF", "-DBUILD_FEXCONFIG=OFF",
                "-DBUILD_THUNKS=OFF", "-DENABLE_LTO=OFF", "-DENABLE_OFFLINE_TELEMETRY=OFF",
                "-DTUNE_CPU=neoverse-n1", "-DUSE_LINKER=lld"], check=True)
subprocess.run(["cmake", "--build", "/tmp/build", "--parallel", jobs], check=True)
subprocess.run(["cmake", "--install", "/tmp/build"], check=True)
subprocess.run(["ldconfig"], check=True)
binary = next((p for p in (Path("/usr/local/bin/FEX"), Path("/usr/local/bin/FEXInterpreter")) if p.exists()), None)
if binary is None:
    raise SystemExit("O build não instalou um interpretador FEX")
for alias in ("FEX", "FEXInterpreter"):
    path = binary.parent / alias
    if not path.exists():
        path.symlink_to(binary.name)
info = {"fex_version": version, "fex_commit": commit, "fex_source": "https://github.com/FEX-Emu/FEX",
        "interpreter_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
Path("/opt").mkdir(exist_ok=True)
Path("/opt/build-info.json").write_text(json.dumps(info, indent=2))
