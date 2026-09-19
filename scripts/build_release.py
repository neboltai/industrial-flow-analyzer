#!/usr/bin/env python3
"""Build the public distribution archive.

The 0.1.0 archive was produced with an ad-hoc ``zip`` invocation and carried the
``.git`` directory into it. This script replaces that with a reproducible build
that refuses to ship anything it should not.

What it does
------------
* writes ``dist/intralogistics-flow-analyzer-<version>.zip``;
* puts everything under a single ``intralogistics-flow-analyzer/`` root;
* walks an explicit include list and an explicit exclude list, never the whole
  working tree;
* normalises entry order, timestamps and permissions so two builds of the same
  content produce byte-identical archives;
* re-opens the finished archive and **fails** if a forbidden path, a Python
  cache, or something that looks like a secret is inside it.

The repository's ``.git`` directory is never touched; it is only kept out of the
archive.

Usage::

    python scripts/build_release.py                 # build and verify
    python scripts/build_release.py --check-only    # verify an existing archive
    python scripts/build_release.py --output-dir X  # build somewhere else
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "intralogistics-flow-analyzer"

#: Everything a user of the Community Edition needs, and nothing else.
INCLUDE_PATHS: Tuple[str, ...] = (
    "src",
    "tests",
    "schemas",
    "examples",
    "skills",
    "mcp",
    "scripts",
    ".codex-plugin",
    ".claude-plugin",
    ".mcp.json",
    "pyproject.toml",
    "README.md",
    "EDITIONS.md",
    "SUPPORT.md",
    "PUBLICATION.md",
    "plugin.json",
    "mcp.json",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    ".gitignore",
)

#: Directory names that never enter the archive, at any depth.
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".github",  # continuous integration, not needed by an end user
        "dist",
        "build",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        ".tox",
        "node_modules",
        "private",
        "real-data",
    }
)

#: File patterns that never enter the archive.
EXCLUDED_FILE_PATTERNS: Tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.egg-info",
    "*.swp",
    "*.swo",
    "*.tmp",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.log",
    "*.private.json",
    "*.private.csv",
    ".DS_Store",
    "Thumbs.db",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "secrets.json",
    "credentials.json",
)

#: Paths that must never appear inside the archive, checked after the build.
FORBIDDEN_IN_ARCHIVE: Tuple[str, ...] = (
    ".git/",
    ".git\\",
    "__pycache__/",
    ".venv/",
    "dist/",
    "build/",
    ".env",
)

#: Content patterns that look like a credential. A hit fails the build.
SECRET_PATTERNS: Tuple[str, ...] = (
    r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|private[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\baws_secret_access_key\s*[:=]",
    r"\bsk-[A-Za-z0-9]{20,}\b",
    r"(?i)\bpassword\s*[:=]\s*[\"'][^\"']{4,}[\"']",
)

#: A fixed timestamp keeps the archive reproducible. 1980-01-01 is the earliest
#: value the ZIP format can represent.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ReleaseError(RuntimeError):
    """Raised when the archive would ship something it must not."""


def read_version() -> str:
    """Single source of truth: the engine constant."""
    text = (REPO_ROOT / "src" / "intralogistics_flow_analyzer" / "models.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^ENGINE_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:  # pragma: no cover - the constant is part of the public API
        raise ReleaseError("ENGINE_VERSION not found in models.py")
    return match.group(1)


def _is_excluded_file(path: Path) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path.name, pattern) for pattern in EXCLUDED_FILE_PATTERNS)


def collect_files(root: Path = REPO_ROOT) -> List[Path]:
    """Every file that belongs in the archive, sorted for reproducibility."""
    collected: List[Path] = []
    for entry in INCLUDE_PATHS:
        source = root / entry
        if not source.exists():
            raise ReleaseError(f"declared include path is missing: {entry}")
        if source.is_file():
            if not _is_excluded_file(source):
                collected.append(source)
            continue
        for candidate in source.rglob("*"):
            if candidate.is_symlink():
                raise ReleaseError(f"symlink cannot enter a release: {candidate.relative_to(root)}")
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if EXCLUDED_DIRECTORIES.intersection(relative.parts[:-1]) or any(part.endswith(".egg-info") for part in relative.parts[:-1]):
                continue
            if _is_excluded_file(candidate):
                continue
            collected.append(candidate)
    return sorted(set(collected), key=lambda p: p.relative_to(root).as_posix())


def build(output_dir: Path, root: Path = REPO_ROOT) -> Path:
    version = read_version()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{PACKAGE_NAME}-{version}.zip"
    files = collect_files(root)
    if not files:  # pragma: no cover - defensive
        raise ReleaseError("nothing to package")

    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = f"{PACKAGE_NAME}/{path.relative_to(root).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
            # A fixed mode keeps the archive identical across machines with
            # different umasks. Scripts stay readable; nothing needs +x.
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return archive_path


def verify(archive_path: Path) -> List[str]:
    """Re-open the archive and prove it carries nothing forbidden."""
    problems: List[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if not names:
            problems.append("the archive is empty")
        roots = {name.split("/", 1)[0] for name in names}
        if roots != {PACKAGE_NAME}:
            problems.append(f"archive root is {sorted(roots)}, expected ['{PACKAGE_NAME}']")

        for name in names:
            inner = name.split("/", 1)[1] if "/" in name else name
            for forbidden in FORBIDDEN_IN_ARCHIVE:
                if inner.startswith(forbidden) or f"/{forbidden}" in f"/{inner}":
                    problems.append(f"forbidden path in archive: {name}")
            if inner.endswith((".pyc", ".pyo", ".pyd")):
                problems.append(f"compiled Python in archive: {name}")
            if "__pycache__" in inner.split("/"):
                problems.append(f"Python cache in archive: {name}")

        for name in names:
            if name.endswith("/"):
                continue
            if Path(name).suffix.lower() in {".png", ".jpg", ".gif", ".pdf", ".ico", ".zip"}:
                continue
            try:
                text = archive.read(name).decode("utf-8")
            except (UnicodeDecodeError, KeyError):
                continue
            for pattern in SECRET_PATTERNS:
                if re.search(pattern, text):
                    problems.append(f"possible secret in {name} (pattern {pattern})")

        required = [
            f"{PACKAGE_NAME}/README.md",
            f"{PACKAGE_NAME}/LICENSE",
            f"{PACKAGE_NAME}/pyproject.toml",
            f"{PACKAGE_NAME}/.mcp.json",
            f"{PACKAGE_NAME}/mcp/server.py",
            f"{PACKAGE_NAME}/src/intralogistics_flow_analyzer/cli.py",
            f"{PACKAGE_NAME}/schemas/dataset.schema.json",
            f"{PACKAGE_NAME}/skills/intralogistics-flow-analyzer/SKILL.md",
            f"{PACKAGE_NAME}/examples/fictional-small-warehouse/dataset.json",
        ]
        for name in required:
            if name not in names:
                problems.append(f"missing from archive: {name}")
    return problems


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify the existing archive instead of rebuilding it",
    )
    args = parser.parse_args(argv)

    version = read_version()
    archive_path = args.output_dir / f"{PACKAGE_NAME}-{version}.zip"
    if args.check_only:
        if not archive_path.is_file():
            print(f"error: {archive_path} does not exist", file=sys.stderr)
            return 1
    else:
        archive_path = build(args.output_dir)

    problems = verify(archive_path)
    if problems:
        print(f"Release archive REJECTED: {archive_path}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(archive_path) as archive:
        entries = len(archive.namelist())
    size = archive_path.stat().st_size
    print(f"Release archive: {archive_path}")
    print(f"  version: {version}")
    print(f"  entries: {entries}")
    print(f"  size: {size} bytes ({size / 1024:.1f} KiB)")
    checksum = digest(archive_path)
    archive_path.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive_path.name}\n", encoding="utf-8")
    print(f"  sha256: {checksum}")
    print("  verified: no .git, no Python cache, no secret, required files present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
