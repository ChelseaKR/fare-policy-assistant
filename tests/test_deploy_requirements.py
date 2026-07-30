"""Guards the hash-pinned rider deploy bundle (roadmap M-7 / audit P1-6).

`infra/deploy.sh` installs the rider bundle only from
`infra/requirements-deploy.txt` under `--require-hashes`, and that file is a
`uv export` of the locked runtime dependency set, so the deployed artifact is
built from exactly the versions the test suite ran against. Before this,
the script installed from loose ranges, and the resolver really did drift:
the locked numpy publishes no manylinux2014 wheels, so the old
manylinux2014-targeted install silently deployed an older numpy than the
tested tree.

These tests call neither AWS nor the network; they hold the script, the pin
file, and uv.lock in lockstep. Regenerate the pin file with `make
deploy-reqs` after any dependency change.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from assistant import config

DEPLOY_SH = config.REPO_ROOT / "infra" / "deploy.sh"
REQUIREMENTS = config.REPO_ROOT / "infra" / "requirements-deploy.txt"
UV_LOCK = config.REPO_ROOT / "uv.lock"

PROJECT_NAME = "fare-policy-assistant"


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _requirement_lines() -> list[str]:
    """Logical requirement lines: comments stripped, continuations joined."""
    text = REQUIREMENTS.read_text(encoding="utf-8")
    joined = text.replace("\\\n", " ")
    lines: list[str] = []
    for raw in joined.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _pinned_versions() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _requirement_lines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^ ;\\]+)", line)
        assert match, f"requirement is not exact-pinned: {line!r}"
        pins[_normalize(match.group(1))] = match.group(2)
    return pins


def _lock_packages() -> dict[str, list[dict[str, Any]]]:
    with UV_LOCK.open("rb") as fh:
        lock = tomllib.load(fh)
    packages: dict[str, list[dict[str, Any]]] = {}
    for package in lock["package"]:
        packages.setdefault(_normalize(package["name"]), []).append(package)
    return packages


def _lock_hashes(package: dict[str, Any]) -> set[str]:
    hashes: set[str] = set()
    sdist = package.get("sdist")
    if isinstance(sdist, dict) and "hash" in sdist:
        hashes.add(sdist["hash"].removeprefix("sha256:"))
    for wheel in package.get("wheels", []):
        if "hash" in wheel:
            hashes.add(wheel["hash"].removeprefix("sha256:"))
    return hashes


def _runtime_closure(packages: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Names reachable from the project's runtime deps (with extras) in uv.lock."""
    project = packages[PROJECT_NAME][0]
    queue: list[tuple[str, tuple[str, ...]]] = [
        (_normalize(dep["name"]), tuple(dep.get("extra", ())))
        for dep in project.get("dependencies", [])
    ]
    seen: set[str] = set()
    while queue:
        name, extras = queue.pop()
        key = name
        if key in seen and not extras:
            continue
        seen.add(key)
        for entry in packages.get(name, []):
            deps = list(entry.get("dependencies", []))
            optional = entry.get("optional-dependencies", {})
            for extra in extras:
                deps.extend(optional.get(extra, []))
            for dep in deps:
                dep_name = _normalize(dep["name"])
                dep_extras = tuple(dep.get("extra", ()))
                if dep_name not in seen or dep_extras:
                    queue.append((dep_name, dep_extras))
    return seen


class TestRequirementsFile:
    def test_exists_and_is_nonempty(self):
        assert REQUIREMENTS.is_file()
        assert _requirement_lines(), "expected at least one pinned requirement"

    def test_every_requirement_carries_a_hash(self):
        for line in _requirement_lines():
            assert "--hash=sha256:" in line, f"requirement without a hash: {line!r}"

    def test_pinned_versions_match_uv_lock(self):
        packages = _lock_packages()
        for name, version in _pinned_versions().items():
            assert name in packages, f"{name} is pinned but absent from uv.lock"
            locked = {entry["version"] for entry in packages[name]}
            assert version in locked, (
                f"{name}=={version} disagrees with uv.lock ({sorted(locked)}); "
                "run `make deploy-reqs` after changing dependencies"
            )

    def test_pinned_hashes_match_uv_lock(self):
        packages = _lock_packages()
        for line in _requirement_lines():
            name = _normalize(re.match(r"^([A-Za-z0-9._-]+)==", line).group(1))  # type: ignore[union-attr]
            locked_hashes: set[str] = set()
            for entry in packages[name]:
                locked_hashes |= _lock_hashes(entry)
            for digest in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
                assert digest in locked_hashes, (
                    f"{name}: hash {digest[:12]}… is not recorded in uv.lock; "
                    "run `make deploy-reqs` after changing dependencies"
                )

    def test_pin_set_is_exactly_the_locked_runtime_closure(self):
        """No runtime dep missing (the bundle must import), no dev dep leaked."""
        packages = _lock_packages()
        closure = _runtime_closure(packages)
        pinned = set(_pinned_versions())
        missing = closure - pinned
        assert not missing, f"runtime deps missing from the pin file: {sorted(missing)}"
        extra = pinned - closure
        assert not extra, f"non-runtime deps leaked into the pin file: {sorted(extra)}"

    def test_bundle_top_level_dependencies_are_present(self):
        pinned = set(_pinned_versions())
        for name in ("anthropic", "beautifulsoup4", "httpx", "jsonschema", "pyyaml", "rank-bm25"):
            assert name in pinned, f"expected top-level runtime dep {name} in the pin file"


class TestDeployScriptUsesThePinFile:
    def test_installs_with_require_hashes_from_the_pin_file(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        assert "--require-hashes" in text
        assert 'infra/requirements-deploy.txt"' in text

    def test_no_loose_inline_ranges_remain(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        assert not re.search(r'"[A-Za-z0-9\[\]._-]+>=[0-9]', text), (
            "deploy.sh must not install from inline version ranges; "
            "pin in infra/requirements-deploy.txt instead (M-7/P1-6)"
        )

    def test_deployment_pins_corpus_and_contains_expired_yolobus_source(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        assert "FPA_PINNED_CORPUS_VERSION" in text
        assert "corpus_version; print(corpus_version())" in text
        assert 'DISABLED_DOC_IDS="yolobus-fares"' in text
        assert '"FPA_DISABLED_DOC_IDS": os.environ["FPA_DEPLOY_DISABLED_DOC_IDS"]' in text
        assert 'HISTORY_HMAC_KEY="$(openssl rand -hex 32)"' in text
        assert '"FPA_HISTORY_HMAC_KEY": os.environ["FPA_DEPLOY_HISTORY_HMAC_KEY"]' in text

    def test_deployment_preserves_existing_lambda_environment_and_history_key(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")

        assert "get-function-configuration" in text
        assert "EXISTING_LAMBDA_ENV" in text
        assert "ResourceNotFoundException" in text
        assert "refusing to deploy" in text
        assert "2>/dev/null || printf '{}'" not in text
        assert "EXISTING_HISTORY_HMAC_KEY" in text
        assert 'elif [[ -n "$EXISTING_HISTORY_HMAC_KEY" ]]' in text
        assert "values.update(" in text

    def test_disabled_document_list_can_be_explicitly_empty_and_ids_are_validated(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")

        assert "[[ ${FPA_DISABLED_DOC_IDS+x} ]]" in text
        assert 'if [[ -n "$DISABLED_DOC_IDS"' in text
        assert "unknown disabled document id(s)" in text

    def test_existing_deploy_captures_rollback_and_applies_config_before_code(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        lambda_block = text.split("# ── Lambda", 1)[1].split(
            "# Hard ceiling on parallel Bedrock spend", 1
        )[0]

        assert "fare-assistant-rollback.XXXXXX" in lambda_block
        assert 'configuration.json"' in lambda_block
        assert 'function.zip"' in lambda_block
        assert (
            'assert_managed_release_config "$PUBLISHED_CONFIG" "published version"' in lambda_block
        )
        assert lambda_block.index("update-function-configuration") < lambda_block.index(
            "update-function-code"
        )

    def test_bedrock_metric_counts_guarded_model_calls_not_just_cache_misses(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")

        assert "--filter-pattern '{ $.model_called IS TRUE }'" in text
        assert "--filter-pattern '{ $.cache = \"miss\" }'" not in text
