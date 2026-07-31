from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from scripts.build_lambda_zip import REGULAR_FILE_MODE, ZIP_EPOCH, build_zip


def test_bundle_is_byte_reproducible_across_metadata_changes(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    package = source / "src" / "assistant"
    package.mkdir(parents=True)
    first = package / "__init__.py"
    second = source / "prompts" / "system.txt"
    second.parent.mkdir()
    first.write_text("VALUE = 1\n", encoding="utf-8")
    second.write_text("Ground answers.\n", encoding="utf-8")

    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"
    build_zip(source, first_zip)

    os.chmod(first, 0o600)
    os.chmod(second, 0o755)
    os.utime(first, (1_700_000_000, 1_700_000_000))
    os.utime(second, (1_800_000_000, 1_800_000_000))
    build_zip(source, second_zip)

    assert first_zip.read_bytes() == second_zip.read_bytes()


def test_bundle_has_canonical_order_timestamp_mode_and_exclusions(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    (source / "z").mkdir(parents=True)
    (source / "z" / "last.py").write_text("last\n", encoding="utf-8")
    (source / "a.py").write_text("first\n", encoding="utf-8")
    cache = source / "z" / "__pycache__"
    cache.mkdir()
    (cache / "last.pyc").write_bytes(b"bytecode")
    dist_info = source / "demo-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("metadata\n", encoding="utf-8")
    (dist_info / "RECORD").write_text("unstable record\n", encoding="utf-8")
    console_scripts = source / "bin"
    console_scripts.mkdir()
    (console_scripts / "httpx").write_text(
        "#!/checkout-specific/.venv/bin/python\n",
        encoding="utf-8",
    )

    output = tmp_path / "bundle.zip"
    build_zip(source, output)

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [
            "a.py",
            "demo-1.0.dist-info/METADATA",
            "z/last.py",
        ]
        for info in archive.infolist():
            assert info.date_time == ZIP_EPOCH
            assert info.create_system == 3
            assert info.external_attr >> 16 == REGULAR_FILE_MODE


def test_bundle_rejects_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    target = source / "target.py"
    target.write_text("safe\n", encoding="utf-8")
    link = source / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ValueError, match="unsupported symlink: link.py"):
        build_zip(source, tmp_path / "bundle.zip")


def test_bundle_rejects_empty_source(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()

    with pytest.raises(ValueError, match="contains no files"):
        build_zip(source, tmp_path / "bundle.zip")
