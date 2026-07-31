"""Build a byte-reproducible AWS Lambda ZIP from a prepared bundle directory.

The deploy bundle contains platform wheels whose extracted mtimes vary between
installs.  Ordinary ``zip`` archives preserve those mtimes (and filesystem
metadata), so rebuilding an unchanged Git revision can produce a different
Lambda ``CodeSha256`` and an unnecessary numbered release.

This builder writes only regular files, in lexical POSIX-path order, with a
fixed ZIP timestamp and mode.  Python bytecode caches and wheel ``RECORD``
files remain excluded exactly as they were by the historical shell command.
Unused top-level dependency console scripts are omitted because installers
rewrite their shebangs with checkout-specific interpreter paths.
"""

from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path, PurePosixPath

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
REGULAR_FILE_MODE = stat.S_IFREG | 0o644


def _is_excluded(relative: PurePosixPath) -> bool:
    return (
        (bool(relative.parts) and relative.parts[0] == "bin")
        or ("__pycache__" in relative.parts)
        or (
            relative.name == "RECORD"
            and len(relative.parts) >= 2
            and relative.parts[-2].endswith(".dist-info")
        )
    )


def _bundle_files(source: Path) -> list[tuple[PurePosixPath, Path]]:
    files: list[tuple[PurePosixPath, Path]] = []
    for path in source.rglob("*"):
        relative = PurePosixPath(path.relative_to(source).as_posix())
        if _is_excluded(relative):
            continue
        if path.is_symlink():
            raise ValueError(f"bundle contains unsupported symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"bundle contains unsupported filesystem entry: {relative}")
        files.append((relative, path))
    return sorted(files, key=lambda item: item[0].as_posix())


def build_zip(source: Path, output: Path) -> None:
    """Write ``source`` to ``output`` with stable metadata and entry ordering."""
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"bundle source is not a directory: {source}")
    files = _bundle_files(source)
    if not files:
        raise ValueError(f"bundle source contains no files: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for relative, path in files:
            info = zipfile.ZipInfo(relative.as_posix(), date_time=ZIP_EPOCH)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = REGULAR_FILE_MODE << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="prepared Lambda bundle directory")
    parser.add_argument("output", type=Path, help="destination ZIP path")
    args = parser.parse_args()
    build_zip(args.source, args.output)


if __name__ == "__main__":
    main()
