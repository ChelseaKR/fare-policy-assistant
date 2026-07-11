"""Template extraction (P3-5, "Generalize the harness"): keeps
template/MANIFEST.yaml honest against the actual repo tree, and keeps
scripts/extract_template.py able to produce a working skeleton.

Two failure modes this guards against, both of which happened to
docs/adapting.md in prose before this manifest existed:
  1. A file listed in the manifest gets renamed/deleted and the manifest
     isn't updated (extraction would silently produce a broken skeleton).
  2. A new domain-specific module lands under src/assistant/, evals/, or
     web/ without anyone deciding whether it belongs in "generic" or
     "domain_specific" (extraction would either leak fare content into the
     template or the classification would just be wrong).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extract_template import extract, load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Top-level modules a new domain would plausibly need classified. Kept to
# stable, long-lived directories (not evals/runs/, not egg-info, not
# __pycache__) so this doesn't churn on every unrelated commit.
CLASSIFIABLE_GLOBS = [
    "src/assistant/*.py",
    "evals/*.py",
    "web/*.py",
]
UNCLASSIFIABLE_NAMES = {"__pycache__"}


def _manifest_paths(manifest: dict) -> set[str]:
    paths = set(manifest["generic"])
    paths.update(e["path"] for e in manifest["generic_edit"])
    paths.update(manifest["domain_specific"])
    return paths


def test_manifest_paths_all_exist():
    manifest = load_manifest()
    for rel in manifest["generic"]:
        assert (REPO_ROOT / rel).exists(), f"generic entry {rel!r} does not exist"
    for entry in manifest["generic_edit"]:
        path = entry["path"]
        assert (REPO_ROOT / path).exists(), f"generic_edit entry {path!r} does not exist"
    for rel in manifest["domain_specific"]:
        assert (REPO_ROOT / rel).exists(), f"domain_specific entry {rel!r} does not exist"


def test_every_top_level_module_is_classified():
    manifest = load_manifest()
    known = _manifest_paths(manifest)
    missing = []
    for pattern in CLASSIFIABLE_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if path.name in UNCLASSIFIABLE_NAMES:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel not in known:
                missing.append(rel)
    assert not missing, (
        "New file(s) not classified in template/MANIFEST.yaml as generic, "
        f"generic_edit, or domain_specific: {missing}"
    )


def test_generic_edit_entries_have_marker_and_note():
    manifest = load_manifest()
    for entry in manifest["generic_edit"]:
        assert entry.get("marker"), f"{entry['path']} missing a marker"
        assert entry.get("note", "").strip(), f"{entry['path']} missing a note"


def test_extract_builds_a_skeleton(tmp_path: Path):
    target = tmp_path / "new-domain-assistant"
    reminders = extract(target, dry_run=False)

    manifest = load_manifest()
    for rel in manifest["generic"]:
        src = REPO_ROOT / rel
        dst = target / rel
        assert dst.exists(), f"{rel} was not copied"
        if src.is_file():
            assert dst.read_bytes() == src.read_bytes()

    for entry in manifest["generic_edit"]:
        assert (target / entry["path"]).exists()

    # Domain-specific content must not leak into the skeleton, except
    # src/assistant/domain.py, which is intentionally replaced by the stub
    # (checked separately below).
    for rel in manifest["domain_specific"]:
        if rel == "src/assistant/domain.py":
            continue
        assert not (target / rel).exists(), f"domain-specific {rel} leaked into skeleton"

    # The stub domain profile replaces the shipped one, and is not the
    # transit-specific version.
    domain_py = (target / "src" / "assistant" / "domain.py").read_text()
    assert "TRANSIT" not in domain_py
    assert "MST" not in domain_py

    assert (target / "GETTING_STARTED.md").exists()
    assert len(reminders) == len(manifest["generic_edit"])


def test_extract_refuses_nonempty_target(tmp_path: Path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keepme").write_text("x")
    with pytest.raises(FileExistsError):
        extract(target, dry_run=False)
