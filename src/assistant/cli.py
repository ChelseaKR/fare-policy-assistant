"""Ask one question from the command line.

    uv run python -m assistant.cli "What proof do I need for the veteran fare on MST?"
    uv run python -m assistant.cli --offline "..."   # mock model, no API key needed
"""

from __future__ import annotations

import argparse

from assistant import config
from assistant.answer import answer_question
from assistant.models import get_model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--offline", action="store_true", help="use the mock model")
    args = parser.parse_args()

    cfg = config.Config()
    if args.offline:
        cfg = config.Config(
            models=config.ModelConfig(provider="mock", answer_model="mock", judge_model="mock")
        )
    model = get_model(cfg.models.provider, cfg.models.answer_model)
    result = answer_question(args.question, model=model, cfg=cfg)

    print(result.answer)
    if result.citations:
        print("\nSources:")
        for c in result.citations:
            print(f"  - {c.agency}: {c.title} — {c.url} (fetched {c.fetch_date})")
    if result.guard_flags:
        print(f"\n[guards: {', '.join(result.guard_flags)}]")


if __name__ == "__main__":
    main()
