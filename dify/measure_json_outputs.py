"""Measure real exported LLM attempts locally; never send traces to a service.

Input: JSONL records with case_id, variant (baseline/schema), node_id and attempts.
Each attempt has text (the raw LLM output), or node_error with kind
schema/transport. Export attempts in execution order, including failed attempts.
Example: {"case_id":"case-1","variant":"schema","node_id":"extract",
          "attempts":[{"text":"..."}]}
This measures JSON syntax/schema, not business validity or factual correctness.
"""

import argparse
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

if __package__:
    from .model_output_schemas import MODEL_SCHEMAS
else:
    from model_output_schemas import MODEL_SCHEMAS


VALIDATORS = {name: Draft202012Validator(schema) for name, schema in MODEL_SCHEMAS.items()}


def classify_attempt(attempt: dict, node_id: str) -> str:
    if not isinstance(attempt, dict):
        raise ValueError("Each attempt must be an object")
    if set(attempt) == {"node_error"}:
        error = attempt["node_error"]
        if not isinstance(error, dict) or set(error) != {"kind"} or error["kind"] not in ("schema", "transport"):
            raise ValueError("node_error.kind must be schema or transport")
        return "schema" if error["kind"] == "schema" else "transport"
    if set(attempt) != {"text"} or not isinstance(attempt["text"], str):
        raise ValueError("Each attempt must contain raw text or node_error")
    try:
        output = json.loads(attempt["text"])
    except json.JSONDecodeError:
        return "syntax"
    return "schema" if next(VALIDATORS[node_id].iter_errors(output), None) else "valid"


def summarize(records: list[dict]) -> dict:
    groups: dict[str, list[tuple[str, str]]] = {}
    turns: dict[tuple[str, str], list[tuple[str, str]]] = {}
    seen = set()
    cases: dict[str, set[tuple[str, str]]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"case_id", "variant", "node_id", "attempts"}:
            raise ValueError("Record must contain case_id, variant, node_id, attempts")
        case, variant, node_id, attempts = (record[key] for key in ("case_id", "variant", "node_id", "attempts"))
        if not isinstance(case, str) or not case.strip() or variant not in ("baseline", "schema") or node_id not in MODEL_SCHEMAS:
            raise ValueError("Invalid case_id, variant or node_id")
        key = (case, variant, node_id)
        if key in seen:
            raise ValueError("Duplicate case/variant/node; merge its ordered attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("Every record needs at least one attempt")
        seen.add(key)
        cases.setdefault(variant, set()).add((case, node_id))
        statuses = [classify_attempt(attempt, node_id) for attempt in attempts]
        turns.setdefault((variant, case), []).append((statuses[0], statuses[-1]))
        for group in (variant, variant + ":" + node_id):
            groups.setdefault(group, []).append((statuses[0], statuses[-1]))
    if not records:
        raise ValueError("No real model records supplied; cannot measure error rate")
    severity = {"valid": 0, "transport": 1, "schema": 2, "syntax": 3}
    for (variant, _), outcomes in turns.items():
        groups.setdefault(variant + ":turn", []).append(tuple(
            max((pair[index] for pair in outcomes), key=severity.__getitem__)
            for index in (0, 1)
        ))
    result = {}
    for group, outcomes in groups.items():
        metrics = {}
        for index, phase in enumerate(("first_attempt", "after_retries")):
            counts = {name: sum(pair[index] == name for pair in outcomes)
                      for name in ("syntax", "schema", "transport", "valid")}
            total = len(outcomes)
            errors = counts["syntax"] + counts["schema"]
            metrics[phase] = {
                "observations": total, **counts,
                "json_failure_rate": errors / total,
                "all_failure_rate": (errors + counts["transport"]) / total,
                "observed_json_target_met": errors / total <= 0.01,
                "zero_failure_95pct_upper_bound": 1 - math.pow(0.05, 1 / total)
                if errors + counts["transport"] == 0 else None,
            }
        result[group] = metrics
    result["paired_cases"] = len(cases.get("baseline", set()) & cases.get("schema", set()))
    result["paired_turns"] = len(
        {case for variant, case in turns if variant == "baseline"}
        & {case for variant, case in turns if variant == "schema"}
    )
    result["unpaired_cases"] = {
        variant: len(items - cases.get("schema" if variant == "baseline" else "baseline", set()))
        for variant, items in cases.items()
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", type=Path, help="Local JSONL export; do not include credentials")
    args = parser.parse_args()
    records = []
    for line_number, line in enumerate(args.traces.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError("Invalid export JSON on line " + str(line_number)) from error
    print(json.dumps(summarize(records), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
