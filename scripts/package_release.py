"""
Build dist/shakti-companion.zip — what install.ps1 downloads.

Contents: every git-tracked file under rag_pipeline/ (minus tests), the
built web UI (rag_pipeline/webui, from `npm run build` in web/), the
sample data, the installer and the README. Untracked local data such as
databases, uploads and config.yaml can never end up in a release.

    cd web && npm ci && npm run build && cd ..
    python scripts/package_release.py
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "shakti-companion.zip"
TRACKED_PREFIXES = ("rag_pipeline/", "sample_data/")
EXCLUDED_PREFIXES = ("rag_pipeline/tests/",)
EXTRA_FILES = ("install.ps1", "README.md", "ARCHITECTURE.md", "LICENSE")


def release_version(value: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value):
        raise argparse.ArgumentTypeError("Use a version such as 0.2.1 or 0.2.1-rc.1.")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Package the companion without local user data.")
    parser.add_argument("--version", type=release_version)
    args = parser.parse_args(argv)
    webui = ROOT / "rag_pipeline" / "webui"
    if not (webui / "index.html").is_file():
        print("rag_pipeline/webui is missing - run `npm ci && npm run build` in web/ first.", file=sys.stderr)
        return 1

    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    files = [f for f in tracked if f.startswith(TRACKED_PREFIXES) and not f.startswith(EXCLUDED_PREFIXES)]
    files += [f for f in EXTRA_FILES if (ROOT / f).is_file()]
    files += [p.relative_to(ROOT).as_posix() for p in webui.rglob("*") if p.is_file()]

    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(set(files)):
            if rel == "rag_pipeline/__init__.py" and args.version:
                source = (ROOT / rel).read_text(encoding="utf-8")
                source, replacements = re.subn(r'^__version__ = "[^"]+"', f'__version__ = "{args.version}"', source, count=1, flags=re.MULTILINE)
                if replacements != 1:
                    raise ValueError("Could not stamp the release version in the companion package.")
                zf.writestr(rel, source)
            else:
                zf.write(ROOT / rel, rel)
    print(f"Wrote {OUT} ({len(set(files))} files, {OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
