#!/usr/bin/env python3
"""Package the kit into agent-kit.zip at the repository root.

    python tools/build_zip.py            # writes ../agent-kit.zip
    python tools/build_zip.py out.zip    # or somewhere else

The archive holds install.py, README.md and the vendor/ folder, wrapped in a
single agent-kit/ directory. It is deterministic (fixed timestamps, sorted
entries, normalised permissions), so rebuilding an unchanged kit produces a
byte-identical file. Re-run it after rebuilding vendor/.
"""

from __future__ import annotations

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


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    count = build(out)
    print(f"wrote {out} ({count} files, {out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
