"""Run the eval set against the configured provider (needs a real API key).

    make eval                                   # all cases
    .venv/bin/python -m tests.evals.run --case vnet-move --json evals.json --min-score 0.8

The provider comes from the usual environment variables (ARCVIZ_PROVIDER, ARCVIZ_MODEL, ANTHROPIC_API_KEY,
OPENAI_API_KEY, ...). Exit codes: 0 ok, 1 mean score below --min-score, 2 usage or provider error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from arcviz.llm import LLMProvider, create_provider
from tests.evals.harness import CaseResult, load_cases, run_case


def _line(r: CaseResult) -> str:
    tokens = sum(r.usage.values())
    ok = sum(1 for c in r.checks if c["ok"])
    failed = "; ".join(c["name"] for c in r.checks if not c["ok"])
    if r.error:
        failed = f"[{r.error[:160]}] {failed}"
    mark = "PASS" if r.passed else "FAIL"
    return f"{mark} {r.id:<34} {r.score:5.2f}  {ok:>2}/{len(r.checks):<2} ops {r.operations:>2}  {tokens:>7} tok  {r.seconds:6.1f}s  {failed}"


def main(argv: list[str] | None = None, provider_factory: Callable[[], LLMProvider] = create_provider) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.evals.run", description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=[], help="run only this case id (repeatable)")
    parser.add_argument("--json", type=Path, help="write the full results to this file")
    parser.add_argument("--min-score", type=float, help="exit 1 if the mean score is below this value")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.case:
        unknown = sorted(set(args.case) - {c["id"] for c in cases})
        if unknown:
            print(f"Unknown case id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in args.case]
    try:
        provider = provider_factory()
    except Exception as exc:
        print(f"Cannot create the provider: {exc}", file=sys.stderr)
        return 2

    print(f"Running {len(cases)} case(s) against {provider.name} · {provider.model}")
    results: list[CaseResult] = []
    for case in cases:
        result = run_case(provider, case)
        results.append(result)
        print(_line(result), flush=True)

    mean = sum(r.score for r in results) / len(results) if results else 0.0
    tokens = sum(sum(r.usage.values()) for r in results)
    print(f"\nmean score {mean:.2f} · {sum(r.passed for r in results)}/{len(results)} cases passed · {tokens} tokens")
    if args.json:
        report = {
            "provider": provider.name,
            "model": provider.model,
            "mean_score": mean,
            "cases": [r.to_dict() for r in results],
        }
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.min_score is not None and mean < args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
