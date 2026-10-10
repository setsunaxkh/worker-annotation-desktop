"""Fetch only manifest-pinned, hash-verified native runtime (no user media)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_verified(url: str, digest: str, target: Path) -> None:
    if target.exists() and sha256(target) == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "worker-annotation-desktop/0.1.0"})
    with urllib.request.urlopen(req, timeout=120) as response, part.open("wb") as out:
        while block := response.read(1024 * 1024):
            out.write(block)
    actual = sha256(part)
    if actual != digest:
        raise RuntimeError(f"SHA256 mismatch for {target.name}: expected {digest}, got {actual}")
    part.replace(target)


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This runtime is for Windows x64 only")
    manifest = json.loads((ROOT / "runtime/manifest.json").read_text(encoding="utf-8"))
    destination = ROOT / manifest["archive_destination"]
    dll = destination / "libmpv-2.dll"
    if dll.is_file() and sha256(dll) == manifest["dll_sha256"]:
        print(f"Verified installed native runtime: {dll}")
        return
    cache = ROOT / ".cache/mpv"
    archive = cache / manifest["asset"]
    download_verified(manifest["url"], manifest["sha256"], archive)
    extractor = manifest["extractor"]
    executable = cache / extractor["name"]
    download_verified(extractor["url"], extractor["sha256"], executable)
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(executable), "x", str(archive), f"-o{destination}", "-y"], check=True)
    if not dll.is_file():
        raise RuntimeError(f"libmpv DLL missing after extraction: {dll}")
    if sha256(dll) != manifest["dll_sha256"]:
        raise RuntimeError(f"libmpv DLL hash mismatch after extraction: {dll}")
    receipt = {"archive_sha256": manifest["sha256"], "dll_sha256": sha256(dll), "dll": dll.name}
    (destination / "verified.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Verified native runtime: {dll}")


if __name__ == "__main__":
    main()
