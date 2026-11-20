from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INPUT_PATH = ROOT / "bn_plan_tags.json"
OUTPUT_PATH = ROOT / "bn_tags_all.json"


def main() -> None:
    plans = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    if not isinstance(plans, list):
        raise ValueError("Input JSON must contain an array of plans")

    tags_all: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        if not isinstance(plan, dict) or not isinstance(plan.get("tag"), list):
            raise ValueError("Each plan must contain a tag array")
        for tag in plan["tag"]:
            if not isinstance(tag, str):
                raise ValueError("Tags must be strings")
            if tag not in seen:
                seen.add(tag)
                tags_all.append(tag)

    result = {"tags_all": tags_all}
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(tags_all)} unique tags to {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
