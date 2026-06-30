"""Embeddable widget: a compact, frameable variant of the assistant.

An agency can drop the assistant into its own fare page with one iframe:

    <iframe src="https://<demo-host>/embed" title="Transit fare policy assistant"
            width="100%" height="520" style="border:1px solid #d6d3cb;border-radius:8px">
    </iframe>

The widget is served from this origin, so its call to /api/ask stays same-origin
under the same connect-src 'self' policy. Framing is governed by a CSP
frame-ancestors header set in web/handler.py (configurable; see that file), not
by anything here. The page keeps the reference-implementation notice and the
"does not decide eligibility / does not collect personal information" line, so
the limits travel with the embed.

Single-turn on purpose: the widget is a doorway to the full assistant (a link
opens it), not a replacement for it.
"""

from __future__ import annotations

EMBED_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit fare policy assistant (embedded widget)</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif; color: #1a1f24; background: #fff;
    line-height: 1.5; font-size: 0.95rem; }
  .wrap { padding: 0.8rem; max-width: 38rem; margin: 0 auto; }
  h1 { font-size: 1.05rem; margin: 0 0 0.3rem; }
  .note { color: #4d5860; font-size: 0.85rem; margin: 0 0 0.6rem; }
  label { display: block; font-weight: 600; margin-bottom: 0.3rem; }
  textarea { width: 100%; min-height: 3rem; font: inherit; padding: 0.5rem;
    border: 1px solid #d6d3cb; border-radius: 6px; background: #fff; color: #1a1f24; }
  textarea:focus-visible, button:focus-visible, a:focus-visible {
    outline: 3px solid #1d4ed8; outline-offset: 2px; }
  button { font: inherit; border: 1px solid #14532d; background: #14532d;
    color: #fff; border-radius: 6px; padding: 0.5rem 1.1rem;
    /* WCAG 2.2 AA 2.5.8 Target Size (Minimum): at least 24px. */
    min-height: 2.5rem; cursor: pointer; margin-top: 0.5rem; }
  button[disabled] { opacity: 0.6; cursor: wait; }
  #status { color: #4d5860; min-height: 1.2rem; margin-top: 0.5rem; font-size: 0.85rem; }
  #status.error { color: #991b1b; }
  #answer p { margin: 0.4rem 0; }
  #answer ul { margin: 0.3rem 0 0.3rem 1.2rem; padding: 0; }
  .sources { border-top: 1px solid #d6d3cb; margin-top: 0.6rem; padding-top: 0.5rem;
    font-size: 0.85rem; }
  .sources a { color: #1d4ed8; }
  .asof { color: #4d5860; font-size: 0.8rem; margin-top: 0.4rem; }
  .ref { color: #4d5860; font-size: 0.78rem; margin-top: 0.8rem;
    border-top: 1px solid #d6d3cb; padding-top: 0.5rem; }
  .ref a { color: #1d4ed8; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Transit fare policy assistant</h1>
  <p class="note">Explains published fare and reduced-fare policy in English or
    Spanish. It does not decide your eligibility and does not collect personal
    information.</p>
  <form id="form">
    <label for="q">Your question</label>
    <textarea id="q" name="question" maxlength="500" required
      placeholder="Example: Senior discount on SBMTD?"></textarea>
    <button type="submit" class="primary" id="submit">Ask</button>
  </form>
  <p id="status" role="status" aria-live="polite"></p>
  <div id="answer"></div>
  <p class="ref">Reference implementation, not an official agency service. Confirm
    important details with the agency.
    <a href="/" target="_blank" rel="noopener">Open the full assistant</a>.</p>
</div>
<script>
(function () {
  "use strict";
  var form = document.getElementById("form");
  var input = document.getElementById("q");
  var submit = document.getElementById("submit");
  var status = document.getElementById("status");
  var answer = document.getElementById("answer");

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Answers are plain text with [doc:id] markers, **bold**, and "- " bullets.
  function render(text) {
    var cleaned = text.replace(/\\s*\\[doc:[a-z0-9-]+\\]/g, "");
    var lines = cleaned.split(/\\n/);
    var html = "";
    var inList = false;
    lines.forEach(function (line) {
      var t = line.trim();
      var item = /^[-\\u2022]\\s+/.test(t);
      if (item && !inList) { html += "<ul>"; inList = true; }
      if (!item && inList) { html += "</ul>"; inList = false; }
      if (!t) { return; }
      var body = esc(t.replace(/^[-\\u2022]\\s+/, ""))
        .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
      html += item ? "<li>" + body + "</li>" : "<p>" + body + "</p>";
    });
    if (inList) { html += "</ul>"; }
    return html;
  }

  function appendSources(container, citations) {
    if (!citations || !citations.length) { return; }
    var src = document.createElement("div");
    src.className = "sources";
    var label = document.createElement("strong");
    label.textContent = "Sources";
    src.appendChild(label);
    var ul = document.createElement("ul");
    citations.forEach(function (c) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = c.url;            // set as a property: no attribute-injection risk
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = c.agency + ": " + c.title;
      li.appendChild(a);
      li.appendChild(document.createTextNode(" (fetched " + c.fetch_date + ")"));
      ul.appendChild(li);
    });
    src.appendChild(ul);
    container.appendChild(src);
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var question = input.value.trim();
    if (!question) { return; }
    submit.disabled = true;
    status.className = "";
    status.textContent = "Looking through the published policies\\u2026";
    answer.innerHTML = "";

    fetch("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: question })
    }).then(function (resp) {
      return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
    }).then(function (r) {
      submit.disabled = false;
      if (!r.ok) {
        status.className = "error";
        status.textContent = r.data.error || "Something went wrong. Please try again.";
        return;
      }
      status.textContent = "";
      var data = r.data;
      var ans = document.createElement("div");
      ans.innerHTML = render(data.answer);
      ans.setAttribute("lang", data.language || "en");
      appendSources(ans, data.citations);
      if (data.as_of_date) {
        var asof = document.createElement("p");
        asof.className = "asof";
        asof.textContent = "Based on policies published as of " + data.as_of_date + ".";
        ans.appendChild(asof);
      }
      answer.appendChild(ans);
    }).catch(function () {
      submit.disabled = false;
      status.className = "error";
      status.textContent = "Could not reach the service. Please try again.";
    });
  });
})();
</script>
</body>
</html>
"""
