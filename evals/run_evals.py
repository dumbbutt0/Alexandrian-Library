"""Run the deterministic evidence-grounding golden suite."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import source_document


DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent
    / "cases"
    / "evidence-grounding-v1.jsonl"
)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """Load newline-delimited immutable golden cases."""
    cases = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid golden case at line {line_number}."
            ) from error
        cases.append(case)
    return cases


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one quote-resolution or abstention expectation."""
    case_id = case["case_id"]
    raw = case["raw_content"].encode("utf-8")
    decoded = case.get("decoded_text", case["raw_content"])
    document = source_document.build_source_document(
        receipt_id=f"source-{case_id}",
        source_content_sha256=hashlib.sha256(raw).hexdigest(),
        content_type=case.get("content_type", "text/plain"),
        title=case.get("title"),
        raw_content=raw,
        decoded_text=decoded,
        charset="utf-8",
        content_truncated=False,
    )
    try:
        locator = source_document.locate_exact_quote(
            document,
            case["exact_quote"],
        )
        outcome = "located"
        detail = locator.locator_id
    except source_document.SourceDocumentError as error:
        message = str(error).lower()
        outcome = "ambiguous" if "ambiguous" in message else "abstained"
        detail = str(error)
    expected = case["expected_outcome"]
    return {
        "case_id": case_id,
        "category": case["category"],
        "expected": expected,
        "actual": outcome,
        "passed": outcome == expected,
        "detail": detail,
    }


def run_suite(path: Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    """Run all cases and calculate deterministic safety rates."""
    results = [evaluate_case(case) for case in load_cases(path)]
    total = len(results)
    passed = sum(result["passed"] for result in results)
    categories = {}
    for result in results:
        bucket = categories.setdefault(
            result["category"],
            {"total": 0, "passed": 0},
        )
        bucket["total"] += 1
        bucket["passed"] += int(result["passed"])
    for bucket in categories.values():
        bucket["rate"] = bucket["passed"] / bucket["total"]
    return {
        "suite": "evidence-grounding-v1",
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0.0,
        "categories": categories,
        "results": results,
    }


def main() -> int:
    report = run_suite()
    summary = {
        key: value
        for key, value in report.items()
        if key != "results"
    }
    summary["failures"] = [
        result
        for result in report["results"]
        if not result["passed"]
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
