"""CLI tests: the one-question command, driven offline with the mock model.

Behavioral coverage of the glue — assembles config, calls the pipeline, and
prints the answer, sources, and any guard flags — without network or cost.
"""

from __future__ import annotations

from assistant import cli


def test_offline_cited_answer_prints_answer_and_sources(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cli", "--offline", "How much is the MST senior fare?"])
    cli.main()
    out = capsys.readouterr().out
    assert out.strip()                 # an answer was printed
    assert "Sources:" in out           # the mock cites a corpus doc
    assert "fetched" in out             # each source shows its fetch date


def test_offline_pii_question_is_refused_without_sources(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["cli", "--offline", "My SSN is 123-45-6789, what is my fare?"]
    )
    cli.main()
    out = capsys.readouterr().out
    assert "123-45-6789" not in out    # PII never echoed back
    assert "Sources:" not in out        # input-guard refusal: no retrieval, no cites
