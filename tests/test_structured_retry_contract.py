import copy
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from dify.build_structured_variant import SOURCE, build
from dify.model_output_schemas import MODEL_SCHEMAS


WORKFLOW = Path(__file__).resolve().parents[1] / "dify" / "AIO Multi Business Structured.txt"


class StructuredRetryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node["data"] for node in cls.flow["workflow"]["graph"]["nodes"]}

    def test_assessment_schema_matches_prompt_without_redundant_reason(self):
        for schema in (MODEL_SCHEMAS["select"], self.nodes["select"]["structured_output"]["schema"]):
            assessment = schema["properties"]["assessments"]["items"]
            self.assertNotIn("reason", assessment["properties"])
            self.assertEqual(set(assessment["properties"]), set(assessment["required"]))
            self.assertEqual(schema["properties"]["reason"]["type"], "string")

    def test_all_structured_objects_have_explicit_required_fields(self):
        def check(schema):
            if schema.get("type") == "object":
                self.assertEqual(set(schema["properties"]), set(schema["required"]))
                self.assertFalse(schema["additionalProperties"])
                for child in schema["properties"].values():
                    check(child)
            if "items" in schema:
                check(schema["items"])
            for child in schema.get("anyOf", []):
                check(child)

        for node_id in MODEL_SCHEMAS:
            schema = self.nodes[node_id]["structured_output"]["schema"]
            with self.subTest(node=node_id):
                Draft202012Validator.check_schema(schema)
                check(schema)
                self.assertEqual(schema, MODEL_SCHEMAS[node_id])

    def test_two_retries_mean_three_total_attempts(self):
        for node_id in MODEL_SCHEMAS:
            loop = self.nodes[node_id + "_retry_loop"]
            with self.subTest(node=node_id):
                self.assertIn("最多重试2次", loop["title"])
                self.assertEqual(loop["loop_count"], 3)
                self.assertEqual(loop["break_conditions"][0]["value"], False)
                prompt = self.nodes[node_id]["prompt_template"][0]["text"]
                self.assertIn("校验反馈重生成（最多2次）", prompt)

    def test_graph_references_and_embedded_code_remain_valid(self):
        graph = self.flow["workflow"]["graph"]
        self.assertEqual(len(self.nodes), len(graph["nodes"]))
        self.assertEqual(len({edge["id"] for edge in graph["edges"]}), len(graph["edges"]))
        for edge in graph["edges"]:
            self.assertIn(edge["source"], self.nodes)
            self.assertIn(edge["target"], self.nodes)
        for node_id, node in self.nodes.items():
            if node["type"] == "code":
                compile(node["code"], node_id, "exec")
        for node_id in MODEL_SCHEMAS:
            node = self.nodes[node_id]
            self.assertTrue(node["structured_output_enabled"])
        self.assertFalse(self.nodes["reply"].get("structured_output_enabled", False))

    def test_captured_null_reason_is_rejected_but_contract_output_passes_real_validators(self):
        output = {
            "assessments": [{"plan_code": "MTOC30G98", "requirements": [], "info_answers": []}],
            "reason": "核对当前候选",
        }
        schema = self.nodes["select"]["structured_output"]["schema"]
        Draft202012Validator(schema).validate(output)
        invalid = copy.deepcopy(output)
        invalid["assessments"][0]["reason"] = None
        self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
        arguments = {
            "candidates_json": json.dumps([{"plan_code": "MTOC30G98", "markdown": "月费：98元"}]),
            "needs_json": json.dumps({"requirements": [], "questions": []}),
        }
        for node_id in ("select_validation", "validate_selection"):
            namespace = {}
            exec(compile(self.nodes[node_id]["code"], node_id, "exec"), namespace)
            if node_id == "select_validation":
                result = namespace["main"](llm_output=output, **arguments)
                self.assertFalse(result["retry"], result["error"])
                self.assertEqual(json.loads(result["text"]), output)
            else:
                result = namespace["main"](llm_output=json.dumps(output), **arguments)
                self.assertEqual(result["error"], "")
                self.assertEqual(json.loads(result["recommendations_json"])[0]["plan_code"], "MTOC30G98")

    def test_selection_rejects_bad_codes_and_preserves_assessment_order(self):
        candidates = [
            {"plan_code": code, "markdown": "月费：98元"} for code in ("P1", "P2", "P3", "P4")
        ]
        arguments = {
            "candidates_json": json.dumps(candidates),
            "needs_json": json.dumps({"requirements": [], "questions": []}),
        }
        cases = [
            (["P2", "P1"], True),
            ([], True),
            (["P1", "P1"], False),
            (["P1.EXTRA"], False),
            (["P1", "P2", "P3"], False),
            (["P1", "P2", "P3", "P4"], False),
            ([None], False),
        ]
        for node_id in ("select_validation", "validate_selection"):
            namespace = {}
            exec(compile(self.nodes[node_id]["code"], node_id, "exec"), namespace)
            for codes, valid in cases:
                with self.subTest(node=node_id, codes=codes):
                    output = {
                        "assessments": [
                            {"plan_code": code, "requirements": [], "info_answers": []} for code in codes
                        ],
                        "reason": "核对当前候选",
                    }
                    value = output if node_id == "select_validation" else json.dumps(output)
                    result = namespace["main"](llm_output=value, **arguments)
                    self.assertEqual(not bool(result["error"]), valid, result)
                    if valid and node_id == "validate_selection":
                        self.assertEqual(
                            [plan["plan_code"] for plan in json.loads(result["recommendations_json"])], codes
                        )

    def test_select_model_does_not_repeat_codes_in_schema_or_examples(self):
        data = self.nodes["select"]
        schema = data["structured_output"]["schema"]
        self.assertEqual(set(schema["properties"]), {"assessments", "reason"})
        self.assertNotIn('"selected_plan_codes"', data["prompt_template"][0]["text"])

    def test_generator_preserves_new_selection_contract(self):
        flow = build(yaml.safe_load(SOURCE.read_text(encoding="utf-8")))
        nodes = {node["id"]: node["data"] for node in flow["workflow"]["graph"]["nodes"]}
        self.assertEqual(nodes["select"]["structured_output"]["schema"], MODEL_SCHEMAS["select"])
        prompt = nodes["select"]["prompt_template"][0]["text"]
        self.assertNotIn('"selected_plan_codes"', prompt)
        for line in prompt.splitlines():
            if line.strip().startswith("{") and "|" not in line:
                Draft202012Validator(MODEL_SCHEMAS["select"]).validate(json.loads(line))
        namespace = {}
        exec(compile(nodes["select_validation"]["code"], "select_validation", "exec"), namespace)
        result = namespace["main"](
            llm_output={"assessments": [{"plan_code": "P1", "requirements": [], "info_answers": []}],
                        "reason": ""},
            candidates_json=json.dumps([{"plan_code": "P1", "markdown": "原文"}]),
            needs_json=json.dumps({"requirements": [], "questions": []}),
        )
        self.assertFalse(result["retry"], result["error"])

    def test_question_coverage_and_original_quotes_remain_enforced(self):
        namespace = {}
        exec(compile(self.nodes["validate_selection"]["code"], "validate_selection", "exec"), namespace)
        answer = {"question_id": "Q1", "status": "answered", "quote": "月费：98元", "answer": "月费98元"}
        assessment = {"plan_code": "P1", "requirements": [], "info_answers": [answer]}
        arguments = {
            "candidates_json": json.dumps([{"plan_code": "P1", "markdown": "月费：98元"}]),
            "needs_json": json.dumps({"requirements": [], "questions": [{"id": "Q1"}]}),
        }
        def validate(item):
            return namespace["main"](
                llm_output=json.dumps({"assessments": [item], "reason": ""}), **arguments
            )
        self.assertEqual(validate(assessment)["error"], "")
        invalid = copy.deepcopy(assessment)
        invalid["info_answers"][0]["quote"] = "月费：1元"
        self.assertTrue(validate(invalid)["error"])
        invalid["info_answers"] = []
        self.assertTrue(validate(invalid)["error"])


if __name__ == "__main__":
    unittest.main()
