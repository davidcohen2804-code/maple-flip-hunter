"""Release gate for the Friends EXE: the one-file binary must point at the real server only.

Checks (exit 1 on any failure):
  * friends_config.json inside the bundle names an https:// server that is not localhost/127.0.0.1, and a 64-hex public key
  * the binary contains none of the CI/test leftovers (demo tokens, localhost URLs, test keys, the CI port)
  * the edition marker says "friends"
PyInstaller stores the bundled data files compressed, so the check extracts the archive with pyinstxtractor-like
logic: simplest robust path is PyInstaller's own CArchiveReader.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FORBIDDEN = [b"127.0.0.1:8797", b"localhost:8797", b"demo-token", b"DEMO-CFM", b"MFHTest", b"physical_ci_settings",
             b"http://127.0.0.1", b"http://localhost"]


def bundled_files(exe: Path) -> dict[str, bytes]:
    from PyInstaller.archive.readers import CArchiveReader

    r = CArchiveReader(str(exe))
    out = {}
    for name in r.toc:
        if name.replace("\\", "/").endswith(("friends_config.json", "_edition.txt")):
            out[name] = r.extract(name)
    return out


def main(exe_path: str) -> int:
    exe = Path(exe_path)
    data = exe.read_bytes()
    bad = [f.decode() for f in FORBIDDEN if f in data]
    files = bundled_files(exe)
    cfg_raw = next((v for k, v in files.items() if k.replace("\\", "/").endswith("friends_config.json")), None)
    ed_raw = next((v for k, v in files.items() if k.replace("\\", "/").endswith("_edition.txt")), None)
    errors = [f"binary contains forbidden string {b!r}" for b in bad]
    if cfg_raw is None:
        errors.append("friends_config.json not bundled")
    else:
        try:
            cfg = json.loads(cfg_raw)
        except Exception as exc:  # noqa: BLE001
            cfg = {}
            errors.append(f"friends_config.json unreadable: {exc}")
        server = str(cfg.get("server", ""))
        key = str(cfg.get("public_key", ""))
        if not server.startswith("https://"):
            errors.append(f"server URL must be https:// (got {server!r})")
        if re.search(r"localhost|127\.0\.0\.1|example\.com|\[::1\]", server):
            errors.append(f"server URL is a placeholder/local address: {server!r}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            errors.append("public_key is not a 64-hex Ed25519 key")
        print("server:", server, "| public key:", key[:16] + "…")
    if ed_raw is None or ed_raw.strip().lower() != b"friends":
        errors.append(f"edition marker is {ed_raw!r}, expected 'friends'")
    for e in errors:
        print("FAIL:", e)
    print("OK: Friends binary points at the real server, no test leftovers" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
