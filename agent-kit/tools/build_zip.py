#!/usr/bin/env python3
"""Package the kit into agent-kit.zip at the repository root.

    python tools/build_zip.py            # writes ../agent-kit.zip and ../agent-kit.zip.base64.txt
    python tools/build_zip.py out.zip    # or somewhere else (plus out.zip.base64.txt)

The archive holds install.py, README.md and the vendor/ folder, wrapped in a
single agent-kit/ directory. It is deterministic (fixed timestamps, sorted
entries, normalised permissions), so rebuilding an unchanged kit produces a
byte-identical file. Re-run it after rebuilding vendor/.

The .base64.txt copy is the same archive as base64 text wrapped at 76 columns
(the output of `base64 -w 76`), for channels that only carry text. Decode it
with `base64 -d agent-kit.zip.base64.txt > agent-kit.zip`.
"""

from __future__ import annotations

import base64
import stat
import sys
import zipfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = KIT.parent / "agent-kit.zip"
INCLUDE = ("install.py", "README.md", "vendor")
PREFIX = "agent-kit/"
EPOCH = (1980, 1, 1, 0, 0, 0)


def kit_files() -> list[Path]:
    files = []
    for name in INCLUDE:
        path = KIT / name
        files.extend([path] if path.is_file() else (p for p in path.rglob("*") if p.is_file()))
    return sorted(files, key=lambda p: p.relative_to(KIT).as_posix())


def build(out: Path) -> int:
    files = kit_files()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(PREFIX + path.relative_to(KIT).as_posix(), EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3                        # unix, so unzip honours the modes
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, path.read_bytes(), compresslevel=9)
    return len(files)


def base64_path(zip_path: Path) -> Path:
    return zip_path.with_name(zip_path.name + ".base64.txt")


def write_base64(zip_path: Path) -> Path:
    out = base64_path(zip_path)
    out.write_bytes(base64.encodebytes(zip_path.read_bytes()))    # 76-column lines, like `base64 -w 76`
    return out


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    count = build(out)
    text = write_base64(out)
    print(f"wrote {out} ({count} files, {out.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote {text} ({text.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
