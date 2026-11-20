"""Clean selected fields in data/new_data/*-cn, preserving all other text.

Run with --dry-run to preview counts, or --directory PATH for another folder.
Only standalone numeric entries are removed; numbers within descriptions remain.
"""

import argparse
import re
from pathlib import Path


DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "data" / "new_data"
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
NUMBER = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
PRICE_FIELDS = {"plan_code", "合约后月费", "违约金"}


def clean_document(text: str) -> tuple[str, int]:
    section = ""
    kept = []
    removed = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        heading = HEADING.fullmatch(stripped)
        if heading:
            section = heading.group(1)
        entry = re.sub(r"^[-*]\s+", "", stripped)
        field = re.split(r"[:：]", entry, maxsplit=1)[0].strip()
        numeric = NUMBER.fullmatch(entry) is not None
        discard = (
            (section == "基础信息" and field == "语言")
            or (section.startswith("价格信息条目") and (field in PRICE_FIELDS or numeric))
            or (section == "优惠政策" and numeric)
        )
        if discard:
            removed += 1
        else:
            kept.append(line)
    return "".join(kept), removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files")
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("Directory does not exist: " + str(args.directory))
    paths = sorted(path for path in args.directory.glob("*-cn") if path.is_file())
    if not paths:
        parser.error("No *-cn documents found in " + str(args.directory))
    changed = total = 0
    for path in paths:
        original = path.read_bytes()
        text, removed = clean_document(original.decode("utf-8-sig"))
        if not removed:
            continue
        output = text.encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf"):
            output = b"\xef\xbb\xbf" + output
        if not args.dry_run:
            path.write_bytes(output)
        changed += 1
        total += removed
        print(f"{path.name}: {removed} lines")
    action = "Would update" if args.dry_run else "Updated"
    print(f"{action} {changed}/{len(paths)} documents; removed {total} lines.")


if __name__ == "__main__":
    main()
