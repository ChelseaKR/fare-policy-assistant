"""Agency operator console (EXP-09, `docs/ideation/03-expansions.md`).

Today, approving a corpus version, reviewing what changed, and setting the
embed widget's allowed origins all mean editing the rider Lambda's environment
variables by hand — a mechanism (R2-6/RR-era work: the pin flow, `/version`,
`diff_corpus`) with no operator experience. No agency comms manager (persona
P12) will ever do that. This module is the operator experience: a small
authenticated surface that turns those three actions into a page with a button.

Deployment model (the design question EXP-09 names as central): this is its
own Lambda handler (`console_handler`), built and deployed by
`infra/deploy-console.sh` as a *second*, separate function with its own API
Gateway route and its own least-privilege IAM role — never merged into the
rider-facing `web/handler.py` Lambda, and never granted anything beyond
`lambda:GetFunctionConfiguration` / `lambda:UpdateFunctionConfiguration` scoped
to that one rider function's ARN. No rider data exists to expose here (no
question or answer text ever reaches this module); the actions are config
reads/writes only.

Authentication fails closed: every request must carry
`Authorization: Bearer <token>` matching `FPA_CONSOLE_TOKEN`, and if that
variable is unset the console refuses every request rather than defaulting
open. A shared bearer token is adequate for a single-operator pilot deploy but
is not real identity — anyone holding it has full access. Production
deployments must put an API Gateway JWT or IAM authorizer in front, backed by
the agency's own SSO/IdP, exactly as `infra/deploy-console.sh` documents; that
step needs a live identity provider to wire up and is intentionally left as a
documented manual step (the same shape as the AWS Budget setup in
`infra/README.md`), not something this module can fake.

Corpus version history is git-backed but the console never shells out to git
at request time (the standard Lambda Python runtime ships no git binary): a
development/CI step (`make history`) walks git history into
`corpus/version_history.json` (see `assistant.corpus.version_history`), and
`infra/deploy-console.sh` bundles that static file. `list_versions` /
`version_diff` read it; if it is missing (e.g. running the console locally
from a full checkout without having generated it), they fall back to a live
git query for developer convenience only.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # bundle root mirrors the repo root
sys.path.insert(0, str(_ROOT / "src"))

from assistant import config  # noqa: E402
from assistant.corpus import corpus_summary, diff_corpus  # noqa: E402
from assistant.ingest import Chunk  # noqa: E402

MAX_BODY_BYTES = 16 * 1024
VERSION_HISTORY_PATH = config.CORPUS_DIR / "version_history.json"

_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'"
    ),
}

# Test/dev hook: set to a zero-arg callable returning a fake "lambda" client
# (an object with get_function_configuration / update_function_configuration
# methods) so pin/embed-config actions are testable without AWS credentials.
_client_factory = None


def _response(status: int, body: str, content_type: str = "application/json") -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": content_type, **_SECURITY_HEADERS},
        "body": body,
    }


def _json(status: int, payload: dict) -> dict:
    return _response(status, json.dumps(payload, ensure_ascii=False))


# ── auth ─────────────────────────────────────────────────────────────────────


def _authorized(event: dict) -> bool:
    token = os.environ.get("FPA_CONSOLE_TOKEN")
    if not token:
        return False  # unset means misconfigured: refuse everything, never default open
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[len("Bearer ") :], token)


# ── corpus version history + diff ───────────────────────────────────────────


def _chunks_from_dicts(raw: list[dict]) -> list[Chunk]:
    return [Chunk(**c) for c in raw]


def _load_version_history() -> list[dict]:
    if VERSION_HISTORY_PATH.exists():
        data = json.loads(VERSION_HISTORY_PATH.read_text(encoding="utf-8"))
        return data.get("versions", [])
    # Developer-convenience fallback: no static file, but a live checkout with
    # git is present. Never runs in the deployed Lambda (see module docstring).
    from assistant.corpus import version_history

    return version_history()


def list_versions() -> list[dict]:
    """The changelog view: every known version without its chunk payload
    (kept out of the list response; `version_diff` loads it on demand)."""
    return [{k: v for k, v in entry.items() if k != "chunks"} for entry in _load_version_history()]


def _find_version(versions: list[dict], ref: str) -> dict | None:
    for entry in versions:
        if ref in (entry.get("commit"), entry.get("corpus_version")):
            return entry
    return None


def version_diff(from_ref: str, to_ref: str) -> dict:
    """Diff two known versions, identified by either their commit id or their
    corpus_version hash (either is a valid handle an operator might paste)."""
    versions = _load_version_history()
    old, new = _find_version(versions, from_ref), _find_version(versions, to_ref)
    if old is None:
        raise LookupError(f"unknown version: {from_ref}")
    if new is None:
        raise LookupError(f"unknown version: {to_ref}")
    result = diff_corpus(_chunks_from_dicts(old["chunks"]), _chunks_from_dicts(new["chunks"]))
    result["from"] = {"commit": old["commit"], "corpus_version": old["corpus_version"]}
    result["to"] = {"commit": new["commit"], "corpus_version": new["corpus_version"]}
    return result


# ── rider Lambda config actions (the "no more editing env vars by hand" bit) ─


def _lambda_client():
    if _client_factory is not None:
        return _client_factory()
    import boto3

    return boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "us-west-2"))


def _rider_function_name() -> str:
    name = os.environ.get("FPA_RIDER_FUNCTION_NAME")
    if not name:
        raise RuntimeError(
            "FPA_RIDER_FUNCTION_NAME is not set; the console does not know which "
            "rider Lambda to update."
        )
    return name


def _patch_rider_env(patch: dict[str, str]) -> dict:
    """Merge `patch` into the rider Lambda's environment variables. Read-modify-
    write, same as `aws lambda update-function-configuration` always is, so a
    pin action never clobbers unrelated settings (answer model, provider,
    FPA_DENSE, ...) it does not manage."""
    client = _lambda_client()
    fn = _rider_function_name()
    current = client.get_function_configuration(FunctionName=fn)
    env = dict(current.get("Environment", {}).get("Variables", {}))
    env.update(patch)
    client.update_function_configuration(FunctionName=fn, Environment={"Variables": env})
    return env


def pin_corpus_version(corpus_version: str) -> dict:
    if not corpus_version or not all(c in "0123456789abcdef" for c in corpus_version.lower()):
        raise ValueError("corpus_version must be a hex digest, e.g. from /console/api/versions")
    return _patch_rider_env({"FPA_PINNED_CORPUS_VERSION": corpus_version})


def _valid_origin(origin: str) -> bool:
    return origin == "'self'" or origin.startswith("https://")


def set_embed_ancestors(ancestors: str) -> dict:
    origins = ancestors.split()
    if not origins or not all(_valid_origin(o) for o in origins):
        raise ValueError("ancestors must be https:// origins (or 'self'), space-separated")
    return _patch_rider_env({"FPA_EMBED_ANCESTORS": " ".join(origins)})


# ── eval report ──────────────────────────────────────────────────────────────


def latest_eval_report() -> dict | None:
    runs_dir = config.EVAL_RUNS_DIR
    if not runs_dir.exists():
        return None
    run_dirs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    for run_dir in reversed(run_dirs):
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            return json.loads(summary_path.read_text(encoding="utf-8"))
    return None


# ── routes ───────────────────────────────────────────────────────────────────


def _read_body(event: dict) -> dict:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("request too large")
    return json.loads(body) if body.strip() else {}


def _status(event: dict) -> dict:
    summary = corpus_summary()
    return _json(
        200,
        {
            "corpus": summary,
            "pinned": os.environ.get("FPA_PINNED_CORPUS_VERSION"),
            "embed_ancestors": os.environ.get("FPA_EMBED_ANCESTORS", "'self'"),
            "rider_function": os.environ.get("FPA_RIDER_FUNCTION_NAME"),
        },
    )


def _versions(event: dict) -> dict:
    try:
        return _json(200, {"versions": list_versions()})
    except RuntimeError as exc:
        return _json(503, {"error": f"Version history unavailable: {exc}"})


def _diff(event: dict) -> dict:
    qs = event.get("queryStringParameters") or {}
    from_ref, to_ref = qs.get("from"), qs.get("to")
    if not from_ref or not to_ref:
        return _json(400, {"error": "Provide ?from=<commit-or-version>&to=<commit-or-version>."})
    try:
        return _json(200, version_diff(from_ref, to_ref))
    except LookupError as exc:
        return _json(404, {"error": str(exc)})
    except RuntimeError as exc:
        return _json(503, {"error": f"Version history unavailable: {exc}"})


def _pin(event: dict) -> dict:
    try:
        data = _read_body(event)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _json(400, {"error": "Send JSON."})
    version = data.get("corpus_version")
    if not isinstance(version, str):
        return _json(400, {"error": 'Send {"corpus_version": "<hex digest>"}.'})
    try:
        env = pin_corpus_version(version)
    except ValueError as exc:
        return _json(400, {"error": str(exc)})
    except RuntimeError as exc:
        return _json(500, {"error": str(exc)})
    return _json(200, {"pinned": env.get("FPA_PINNED_CORPUS_VERSION")})


def _embed_config_get(event: dict) -> dict:
    return _json(200, {"ancestors": os.environ.get("FPA_EMBED_ANCESTORS", "'self'")})


def _embed_config_post(event: dict) -> dict:
    try:
        data = _read_body(event)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _json(400, {"error": "Send JSON."})
    ancestors = data.get("ancestors")
    if not isinstance(ancestors, str):
        return _json(400, {"error": 'Send {"ancestors": "https://a.example https://b.example"}.'})
    try:
        env = set_embed_ancestors(ancestors)
    except ValueError as exc:
        return _json(400, {"error": str(exc)})
    except RuntimeError as exc:
        return _json(500, {"error": str(exc)})
    return _json(200, {"ancestors": env.get("FPA_EMBED_ANCESTORS")})


def _eval_report(event: dict) -> dict:
    report = latest_eval_report()
    if report is None:
        return _json(404, {"error": "No eval run recorded yet."})
    return _json(200, report)


_ROUTES = {
    ("GET", "/console/api/status"): _status,
    ("GET", "/console/api/versions"): _versions,
    ("GET", "/console/api/diff"): _diff,
    ("POST", "/console/api/pin"): _pin,
    ("GET", "/console/api/embed-config"): _embed_config_get,
    ("POST", "/console/api/embed-config"): _embed_config_post,
    ("GET", "/console/api/eval-report"): _eval_report,
}


def console_handler(event: dict, context: object = None) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath", "/")

    if path == "/console" and method == "GET":
        if not _authorized(event):
            return _json(401, {"error": "Unauthorized."})
        return _response(200, CONSOLE_HTML, "text/html; charset=utf-8")

    route = _ROUTES.get((method, path))
    if route is None:
        return _json(404, {"error": "Not found."})
    if not _authorized(event):
        return _json(401, {"error": "Unauthorized."})
    try:
        return route(event)
    except Exception as exc:  # never leak internals; never log content
        print(json.dumps({"error": type(exc).__name__, "route": path}))
        return _json(500, {"error": "Something went wrong on the console's side."})


CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fare policy assistant — agency operator console</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif; color: #1a1f24; background: #fff; line-height: 1.5; }
  main { max-width: 46rem; margin: 0 auto; padding: 1.2rem 1rem 3rem; }
  h1 { font-size: 1.3rem; margin: 0 0 0.3rem; }
  h2 { font-size: 1.05rem; margin: 1.6rem 0 0.5rem; }
  .note { color: #4d5860; font-size: 0.9rem; }
  section { border: 1px solid #d6d3cb; border-radius: 8px; padding: 1rem; margin-top: 1rem; }
  label { display: block; font-weight: 600; margin-bottom: 0.3rem; }
  input, textarea { width: 100%; font: inherit; padding: 0.5rem; border: 1px solid #d6d3cb;
    border-radius: 6px; background: #fff; color: #1a1f24; }
  input:focus-visible, textarea:focus-visible, button:focus-visible, a:focus-visible {
    outline: 3px solid #1d4ed8; outline-offset: 2px; }
  button { font: inherit; border: 1px solid #14532d; background: #14532d; color: #fff;
    border-radius: 6px; padding: 0.5rem 1rem; min-height: 2.5rem; cursor: pointer;
    margin-top: 0.5rem; }
  button.secondary { background: #fff; color: #14532d; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #e5e3db; }
  code { background: #f4f2ea; padding: 0.1rem 0.3rem; border-radius: 4px; }
  #status { color: #4d5860; min-height: 1.2rem; margin-top: 0.5rem; font-size: 0.85rem; }
  #status.error { color: #991b1b; }
  .pin-badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
    font-size: 0.8rem; background: #eef2e9; }
</style>
</head>
<body>
<main>
  <h1>Agency operator console</h1>
  <p class="note">Approve a corpus version, review what changed, and set the
    embed widget's allowed origins &mdash; without touching AWS consoles. Reference
    implementation; see <code>infra/README.md</code> for deployment and
    authentication.</p>

  <section aria-labelledby="auth-h">
    <h2 id="auth-h">Access token</h2>
    <label for="token">Console access token</label>
    <input id="token" type="password" autocomplete="off" placeholder="Bearer token">
    <button id="save-token" type="button">Use this token</button>
    <p class="note">Held in this tab only (sessionStorage), never sent anywhere
      but this console's own API.</p>
  </section>

  <section aria-labelledby="status-h">
    <h2 id="status-h">Serving now</h2>
    <div id="status-body">Load the status above to see the live corpus.</div>
    <button id="refresh-status" type="button">Refresh</button>
  </section>

  <section aria-labelledby="versions-h">
    <h2 id="versions-h">Corpus versions</h2>
    <div id="versions-body"></div>
    <button id="refresh-versions" type="button">Load versions</button>
  </section>

  <section aria-labelledby="diff-h">
    <h2 id="diff-h">Compare two versions</h2>
    <label for="diff-from">From (commit or corpus_version)</label>
    <input id="diff-from">
    <label for="diff-to">To (commit or corpus_version)</label>
    <input id="diff-to">
    <button id="run-diff" type="button">Show diff</button>
    <div id="diff-body"></div>
  </section>

  <section aria-labelledby="embed-h">
    <h2 id="embed-h">Embed settings</h2>
    <label for="ancestors">Allowed embed origins (space-separated, https://)</label>
    <textarea id="ancestors" rows="2"></textarea>
    <button id="save-embed" type="button">Save embed settings</button>
  </section>

  <section aria-labelledby="eval-h">
    <h2 id="eval-h">Latest eval report</h2>
    <div id="eval-body"></div>
    <button id="refresh-eval" type="button">Load eval report</button>
  </section>

  <p id="status" role="status" aria-live="polite"></p>
</main>
<script>
(function () {
  "use strict";
  var statusEl = document.getElementById("status");

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function say(msg, isError) {
    statusEl.className = isError ? "error" : "";
    statusEl.textContent = msg;
  }

  function token() {
    return sessionStorage.getItem("fpa_console_token") || "";
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "authorization": "Bearer " + token() }, opts.headers || {});
    return fetch(path, opts).then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    });
  }

  document.getElementById("save-token").addEventListener("click", function () {
    sessionStorage.setItem("fpa_console_token", document.getElementById("token").value.trim());
    say("Token saved for this tab.");
  });

  document.getElementById("refresh-status").addEventListener("click", function () {
    api("/console/api/status").then(function (r) {
      if (!r.ok) { say(r.data.error || "Could not load status.", true); return; }
      var d = r.data;
      var mismatch = "DOES NOT MATCH PIN " + esc(d.pinned);
      var badge = d.pinned
        ? (d.pinned === d.corpus.corpus_version ? "matches pin" : mismatch)
        : "no pin set";
      document.getElementById("status-body").innerHTML =
        "<p>Serving <code>" + esc(d.corpus.corpus_version) + "</code> as of " +
        esc(d.corpus.as_of) + " &mdash; <span class=\\"pin-badge\\">" + badge + "</span></p>" +
        "<p>Agencies: " + esc(d.corpus.agencies.join(", ")) + "</p>" +
        "<p>Embed origins: <code>" + esc(d.embed_ancestors) + "</code></p>";
      say("Status refreshed.");
    }).catch(function () { say("Could not reach the console API.", true); });
  });

  document.getElementById("refresh-versions").addEventListener("click", function () {
    api("/console/api/versions").then(function (r) {
      if (!r.ok) { say(r.data.error || "Could not load versions.", true); return; }
      var rows = r.data.versions.map(function (v) {
        return "<tr><td><code>" + esc(v.corpus_version) + "</code></td><td>" + esc(v.commit) +
          "</td><td>" + esc(v.committed_at) + "</td><td>" + esc(v.documents) +
          "</td><td><button type=\\"button\\" class=\\"secondary pin-btn\\" data-version=\\"" +
          esc(v.corpus_version) + "\\">Pin</button></td></tr>";
      }).join("");
      document.getElementById("versions-body").innerHTML =
        "<table><thead><tr><th>corpus_version</th><th>commit</th><th>committed</th>" +
        "<th>docs</th><th></th></tr></thead><tbody>" + rows + "</tbody></table>";
      document.querySelectorAll(".pin-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          api("/console/api/pin", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ corpus_version: btn.getAttribute("data-version") })
          }).then(function (r) {
            if (!r.ok) { say(r.data.error || "Could not pin.", true); return; }
            say("Pinned " + r.data.pinned + ".");
          }).catch(function () { say("Could not reach the console API.", true); });
        });
      });
      say("Versions loaded.");
    }).catch(function () { say("Could not reach the console API.", true); });
  });

  document.getElementById("run-diff").addEventListener("click", function () {
    var from = document.getElementById("diff-from").value.trim();
    var to = document.getElementById("diff-to").value.trim();
    if (!from || !to) { say("Enter both a from and a to version.", true); return; }
    api("/console/api/diff?from=" + encodeURIComponent(from) + "&to=" + encodeURIComponent(to))
      .then(function (r) {
        if (!r.ok) { say(r.data.error || "Could not load diff.", true); return; }
        var d = r.data;
        function list(items) { return items.length ? "<ul>" + items.map(function (i) {
          return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>" : "<p>none</p>"; }
        document.getElementById("diff-body").innerHTML =
          "<p><strong>Added</strong></p>" + list(d.added) +
          "<p><strong>Removed</strong></p>" + list(d.removed) +
          "<p><strong>Changed</strong></p>" + list(d.changed);
        say("Diff loaded.");
      }).catch(function () { say("Could not reach the console API.", true); });
  });

  document.getElementById("save-embed").addEventListener("click", function () {
    api("/console/api/embed-config", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ancestors: document.getElementById("ancestors").value.trim() })
    }).then(function (r) {
      if (!r.ok) { say(r.data.error || "Could not save embed settings.", true); return; }
      say("Embed origins saved: " + r.data.ancestors);
    }).catch(function () { say("Could not reach the console API.", true); });
  });

  document.getElementById("refresh-eval").addEventListener("click", function () {
    api("/console/api/eval-report").then(function (r) {
      if (!r.ok) { say(r.data.error || "No eval report yet.", true); return; }
      var d = r.data;
      var rows = Object.keys(d.suites || {}).map(function (name) {
        var s = d.suites[name];
        return "<tr><td>" + esc(name) + "</td><td>" + s.passed + "/" + s.total +
          "</td><td>" + (s.pass_rate * 100).toFixed(0) + "%</td></tr>";
      }).join("");
      document.getElementById("eval-body").innerHTML =
        "<p>Run at " + esc(d.run_at) + " (" + esc(d.mode) + ", " +
        (d.offline ? "offline" : "live") + ")</p>" +
        "<table><thead><tr><th>suite</th><th>passed</th><th>rate</th></tr></thead><tbody>" +
        rows + "</tbody></table>";
      say("Eval report loaded.");
    }).catch(function () { say("Could not reach the console API.", true); });
  });
})();
</script>
</body>
</html>
"""
