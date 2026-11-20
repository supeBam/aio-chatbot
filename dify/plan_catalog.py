"""Generate extractive mock API catalogs exclusively from cleaned plan documents."""

import argparse
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "new_data"
BUSINESSES = {"AS": "BN", "MS": "MS", "XE": "XE"}
BASIC_FIELDS = ("项目编码", "方案名称", "服务类型", "套餐参考月费", "合约月数")


def plain_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def parse_document(text: str) -> tuple[dict[str, str], list[tuple[str, list[str]]]]:
    basic: dict[str, str] = {}
    sections: list[tuple[str, list[str]]] = []
    section = ""
    for line in text.splitlines():
        heading = re.fullmatch(r"###\s+(.+?)\s*", line)
        if heading:
            section = heading.group(1)
            sections.append((section, []))
        elif section == "基础信息" and line.strip():
            field, separator, value = line.partition(":")
            if not separator or field in basic or not value.strip():
                raise ValueError("Invalid or duplicate basic field: " + line)
            basic[field] = value.strip()
        elif line.startswith("- ") and sections:
            sections[-1][1].append(line[2:])
    for field in BASIC_FIELDS:
        if not basic.get(field):
            raise ValueError("Missing basic field: " + field)
    if basic["服务类型"] not in BUSINESSES:
        raise ValueError("Unknown service type: " + basic["服务类型"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", basic["项目编码"]):
        raise ValueError("Invalid plan code: " + basic["项目编码"])
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", basic["套餐参考月费"]):
        raise ValueError("Invalid reference monthly fee: " + basic["套餐参考月费"])
    if not basic["合约月数"].isascii() or not basic["合约月数"].isdigit():
        raise ValueError("Invalid contract month count: " + basic["合约月数"])
    return basic, sections


def extract_tags(basic: dict[str, str], sections: list[tuple[str, list[str]]]) -> list[str]:
    business = BUSINESSES[basic["服务类型"]]
    name = basic["方案名称"]
    category = {"BN": "家居宽带", "MS": "移动通讯", "XE": "影音串流"}[business]
    if business == "XE":
        if re.search(r"JOOX|KKBOX", name, re.I):
            category = "音乐串流"
        elif "Global SIM" in name:
            category = "旅游数据"
    tags = [
        category, "方案名称: " + name,
        "套餐参考月费: " + basic["套餐参考月费"],
        "基础合约月数: " + basic["合约月数"],
    ]
    if business == "BN":
        speed = re.match(r"[0-9]+(?:\.[0-9]+)?(?:Gbps|M)", name)
        if speed:
            tags.append(speed.group())
    for title, entries in sections:
        if title == "优惠政策":
            tags.extend(plain_text(entry) for entry in entries if entry != "未提供优惠政策")
        elif title.startswith("价格信息条目"):
            service = next((entry.partition(":")[2].strip() for entry in entries
                            if entry.startswith("服务名称:")), "")
            if not service:
                service = next((entry.partition(":")[2].strip() for entry in entries
                                if entry.startswith("服务内容:")), "")
            if not service:
                raise ValueError("Missing service name or content in " + title)
            for entry in entries:
                field, separator, value = entry.partition(":")
                if not separator:
                    raise ValueError("Invalid price entry in " + title + ": " + entry)
                if field.strip() in {"plan_code", "合约后月费", "违约金"}:
                    raise ValueError("Unclean price field in " + title + ": " + field)
                if field.strip() != "服务名称":
                    tags.append(plain_text("服务: " + service + "；" + field.strip() + ": " + value.strip()))
        elif title.startswith("功能信息条目"):
            for entry in entries:
                if entry == "未提供功能信息集合":
                    continue
                entry = plain_text(entry)
                if len(entry) <= 160:
                    tags.append(entry)
                else:
                    heading, separator, _ = entry.partition(":")
                    if separator and 0 < len(heading) <= 80:
                        tags.append(heading)
    return list(dict.fromkeys(tag for tag in tags if tag))


def load_catalogs(directory: Path = SOURCE) -> tuple[dict[str, list[dict]], dict[str, dict[str, str]]]:
    paths = sorted(path for path in directory.glob("*-cn") if path.is_file())
    if not paths:
        raise ValueError("No plan documents found in " + str(directory))
    catalogs: dict[str, list[dict]] = {business: [] for business in BUSINESSES.values()}
    details: dict[str, dict[str, str]] = {business: {} for business in BUSINESSES.values()}
    seen: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        try:
            basic, sections = parse_document(text)
            code = basic["项目编码"]
            if path.name != code + "-cn" or code in seen:
                raise ValueError("Duplicate plan code or filename mismatch: " + code)
            if re.search(r"^语言[:：]", text, re.M):
                raise ValueError("Unclean language field")
            if any(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", entry.strip())
                   for title, entries in sections
                   if title == "优惠政策" or title.startswith("价格信息条目")
                   for entry in entries):
                raise ValueError("Unclean standalone numeric entry")
            tags = extract_tags(basic, sections)
        except ValueError as error:
            raise ValueError(str(path) + ": " + str(error)) from error
        seen.add(code)
        business = BUSINESSES[basic["服务类型"]]
        catalogs[business].append({"plan_code": code, "tag": tags})
        details[business][code] = text
    return catalogs, details


def write_catalogs(catalogs: dict[str, list[dict]], directory: Path = ROOT) -> None:
    for business, plans in catalogs.items():
        path = directory / (business.lower() + "_plan_tags_new.json")
        path.write_text(json.dumps(plans, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(plans)} plans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    catalogs, _ = load_catalogs()
    write_catalogs(catalogs)
