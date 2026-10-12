"""Generate SHA256 checksums for every delivered file except the checksum table."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "SHA256_checksums.csv"
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path != output
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and path.suffix != ".pyc"
    )
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "size_bytes", "sha256"])
        writer.writeheader()
        for path in paths:
            writer.writerow(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    print(f"checksums written: {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
