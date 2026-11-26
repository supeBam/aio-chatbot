import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from dify.plan_catalog import extract_tags, load_catalogs, parse_document, plain_text


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "new_data"


class LatestPlanDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = {path.name[:-3]: path.read_text(encoding="utf-8-sig")
                         for path in SOURCE.glob("*-cn") if path.is_file()}
        cls.catalogs = {
            business: json.loads((ROOT / (business.lower() + "_plan_tags_new.json")).read_text(encoding="utf-8"))
            for business in ("BN", "MS", "XE")
        }
        cls.flow = yaml.safe_load((ROOT / "dify" / "AIO Multi Business Structured.yml").read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node["data"] for node in cls.flow["workflow"]["graph"]["nodes"]}

    def literal(self, node_id, name):
        for statement in ast.parse(self.nodes[node_id]["code"]).body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
        self.fail("Missing embedded constant: " + name)

    def test_catalogs_match_current_documents_only(self):
        expected = {"BN": set(), "MS": set(), "XE": set()}
        for code, text in self.documents.items():
            service = re.search(r"^服务类型: (.+)$", text, re.M).group(1)
            expected[{"AS": "BN", "MS": "MS", "XE": "XE"}[service]].add(code)
        for business, plans in self.catalogs.items():
            with self.subTest(business=business):
                self.assertEqual(len(plans), len(expected[business]))
                self.assertEqual(len(plans), len({plan["plan_code"] for plan in plans}))
                self.assertEqual({plan["plan_code"] for plan in plans}, expected[business])
                for plan in plans:
                    self.assertEqual(set(plan), {"plan_code", "tag"})
                    self.assertTrue(plan["tag"])
                    self.assertTrue(all(isinstance(tag, str) and tag.strip() for tag in plan["tag"]))
                    self.assertEqual(len(plan["tag"]), len(set(plan["tag"])))

    def test_tags_are_regenerated_from_current_documents(self):
        generated, _ = load_catalogs()
        self.assertEqual(generated, self.catalogs)
        for plans in self.catalogs.values():
            for plan in plans:
                with self.subTest(code=plan["plan_code"]):
                    document = plain_text(self.documents[plan["plan_code"]])
                    for tag in plan["tag"][1:]:
                        if tag.startswith("服务: "):
                            service, fact = tag[4:].split("；", 1)
                            self.assertIn(service, document)
                            field, _, value = fact.partition(": ")
                            self.assertIn(field, document)
                            self.assertIn(value, document)
                        elif tag.startswith("基础合约月数: "):
                            self.assertIn(tag.replace("基础合约月数", "合约月数"), document)
                        else:
                            self.assertIn(plain_text(tag), document)

    def test_workflow_embeds_exact_latest_catalogs_and_documents(self):
        self.assertEqual(self.literal("fetch_tags", "TAG_CATALOGS"), self.catalogs)
        expected = {
            business: {plan["plan_code"]: self.documents[plan["plan_code"]] for plan in plans}
            for business, plans in self.catalogs.items()
        }
        self.assertEqual(self.literal("rank", "DETAILS"), expected)
        self.assertEqual(sum(map(len, expected.values())), len(self.documents))

    def test_old_only_codes_are_absent_from_mock_apis(self):
        retired = {"AX9824OFF", "MC5G30G3BAE", "MC5G100G3BAC", "MTOC30G98", "MTOC5G50GJX",
                   "AX12924DPIQOEX", "AX19924GS5OTT", "AX109243MNEWCDN", "AX9924AXA"} - self.documents.keys()
        for business, plans in self.literal("fetch_tags", "TAG_CATALOGS").items():
            self.assertFalse(retired & {plan["plan_code"] for plan in plans})
            self.assertFalse(retired & self.literal("rank", "DETAILS")[business].keys())
        self.assertIn("AX10924DB_MTSE20G98NO", self.literal("rank", "DETAILS")["BN"])

    def test_generated_details_remain_cleaned(self):
        for code, text in self.documents.items():
            with self.subTest(code=code):
                self.assertNotRegex(text, r"(?m)^语言[:：]")
                self.assertNotRegex(text, r"(?m)^- (?:plan_code|合约后月费|违约金)[:：]")
                self.assertNotRegex(text, r"(?m)^- [0-9]+(?:\.[0-9]+)?\s*$")

    def test_mock_api_execution_returns_only_current_tags_and_exact_details(self):
        tags_api = {}
        exec(compile(self.nodes["fetch_tags"]["code"], "fetch_tags", "exec"), tags_api)
        details_api = {}
        exec(compile(self.nodes["rank"]["code"], "rank", "exec"), details_api)
        for business, plans in self.catalogs.items():
            codes = [plan["plan_code"] for plan in plans]
            with self.subTest(business=business):
                result = tags_api["main"](business, {"bn_scope_codes": codes})
                self.assertEqual(result["bundle"]["status"], "ready")
                self.assertEqual(result["bundle"]["plans_tags"], plans)
                returned = details_api["mock_rank_and_details"](business, plans, codes[::-1])
                self.assertEqual([plan["plan_code"] for plan in returned], codes[::-1])
                for plan in returned:
                    self.assertEqual(plan["markdown"], self.documents[plan["plan_code"]])
        missing_code = "NOT_IN_CURRENT_SOURCE"
        self.assertNotIn(missing_code, self.documents)
        rejected = tags_api["main"]("BN", {"bn_scope_codes": [missing_code]})["bundle"]
        self.assertEqual(rejected["status"], "error")
        self.assertEqual(rejected["plans_tags"], [])
        self.assertTrue(rejected["error"])
        with self.assertRaisesRegex(ValueError, "后端未找到"):
            details_api["mock_rank_and_details"]("MS", self.catalogs["MS"], [missing_code])


class PlanCatalogGenerationTests(unittest.TestCase):
    DOCUMENT = """# 套餐文档 - TEST
### 基础信息
项目编码: TEST
方案名称: 1000M家居寬頻
服务类型: AS
套餐参考月费: 235
合约月数: 23
### 优惠政策
- 每月回贈$20至同時登記之指定寬頻服務計劃
### 价格信息条目 1
- 服务名称: 寬頻
- 优惠月费: 第1至24個月:$109; 預繳$200
- 合约期: 24個月合約
### 价格信息条目 2
- 服务内容: HKBN SAFE
- 优惠月费: 首6個月:$0; 預繳$88
### 功能信息条目 1
- 家長監護功能
- 家長監護功能
"""

    def test_extractive_tags_keep_pricing_scope_and_conditions(self):
        basic, sections = parse_document(self.DOCUMENT)
        tags = extract_tags(basic, sections)
        self.assertIn("1000M", tags)
        self.assertIn("基础合约月数: 23", tags)
        self.assertIn("服务: 寬頻；合约期: 24個月合約", tags)
        self.assertIn("服务: HKBN SAFE；优惠月费: 首6個月:$0; 預繳$88", tags)
        self.assertIn("每月回贈$20至同時登記之指定寬頻服務計劃", tags)
        self.assertEqual(tags.count("家長監護功能"), 1)
        self.assertNotIn("首6个月免费", tags)

    def test_invalid_or_unclean_documents_fail_explicitly(self):
        for text in (
            self.DOCUMENT.replace("服务类型: AS", "服务类型: UNKNOWN"),
            self.DOCUMENT.replace("项目编码: TEST", "项目编码: WRONG"),
            self.DOCUMENT.replace("合约月数: 23", ""),
            self.DOCUMENT.replace("合约月数: 23", "合约月数: 23\n语言: cn"),
            self.DOCUMENT.replace("### 价格信息条目 1", "- 111111\n### 价格信息条目 1"),
            self.DOCUMENT.replace("- 服务名称: 寬頻", "- 服务名称: 寬頻\n- plan_code: TEST"),
        ):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "TEST-cn"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "TEST-cn"):
                    load_catalogs(Path(directory))

    def test_missing_source_has_no_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "No plan documents"):
                load_catalogs(Path(directory))

    def test_source_removals_remove_tags_and_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TEST-cn"
            path.write_text(self.DOCUMENT, encoding="utf-8")
            other = Path(directory) / "SECOND-cn"
            other.write_text(self.DOCUMENT.replace("TEST", "SECOND"), encoding="utf-8")
            before, _ = load_catalogs(Path(directory))
            self.assertEqual(len(before["BN"]), 2)
            other.unlink()
            after, details = load_catalogs(Path(directory))
            self.assertEqual([plan["plan_code"] for plan in after["BN"]], ["TEST"])
            self.assertEqual(set(details["BN"]), {"TEST"})


if __name__ == "__main__":
    unittest.main()
