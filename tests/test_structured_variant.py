import json
import unittest

import yaml
from jsonschema import Draft202012Validator

from dify.build_structured_variant import DESTINATION, SOURCE, build
from dify.model_output_schemas import MODEL_SCHEMAS


class StructuredVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
        cls.flow = yaml.safe_load(DESTINATION.read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node for node in cls.flow["workflow"]["graph"]["nodes"]}
        cls.edges = cls.flow["workflow"]["graph"]["edges"]

    def test_artifact_is_reproducible_and_original_unchanged(self):
        self.assertEqual(self.flow, build(self.original))
        original_nodes = {n["id"]: n["data"] for n in self.original["workflow"]["graph"]["nodes"]}
        for node_id in MODEL_SCHEMAS:
            self.assertNotIn("structured_output", original_nodes[node_id])
            self.assertNotIn("response_format", original_nodes[node_id]["model"]["completion_params"])

    def test_schema_models_and_success_failure_paths(self):
        for node_id, schema in MODEL_SCHEMAS.items():
            with self.subTest(node=node_id):
                Draft202012Validator.check_schema(schema)
                llm = self.nodes[node_id]["data"]
                self.assertEqual(llm["model"]["completion_params"]["response_format"], "json_object")
                self.assertEqual(llm["structured_output"], {"schema": schema})
                self.assertTrue(llm["structured_output_enabled"])
                self.assertEqual(llm["error_strategy"], "fail-branch")
                validation = self.nodes[node_id + "_validation"]["data"]
                model_input = next(v for v in validation["variables"] if v["variable"] == "llm_output")
                self.assertEqual(model_input["value_selector"], [node_id, "structured_output"])
                self.assertEqual(model_input["value_type"], "object")
                update = self.nodes[node_id + "_retry_update"]["data"]["items"]
                current = next(item for item in update if item["variable_selector"][1] == "current_text")
                self.assertEqual(current["value"], [node_id + "_validation", "text"])
                edge_pairs = {(e["source"], e["sourceHandle"], e["target"]) for e in self.edges}
                self.assertIn((node_id, "source", node_id + "_validation"), edge_pairs)
                self.assertIn((node_id, "fail-branch", node_id + "_node_failure"), edge_pairs)
                self.assertIn((node_id + "_node_failure", "source", node_id + "_failure_update"), edge_pairs)
                failure = self.nodes[node_id + "_node_failure"]["data"]
                namespace = {}
                exec(compile(failure["code"], node_id, "exec"), namespace)
                result = namespace["main"]("ProviderError", "failed")
                self.assertEqual(result["retry"], True)
                self.assertEqual(result["text"], "")
                self.assertIn("ProviderError", result["error"])
                self.assertIn("failed", result["error"])
                for item in self.nodes[node_id + "_failure_update"]["data"]["items"]:
                    self.assertEqual(item["value"][0], node_id + "_node_failure")

    def test_examples_follow_schemas(self):
        for node_id, schema in MODEL_SCHEMAS.items():
            prompt = self.nodes[node_id]["data"]["prompt_template"][0]["text"]
            examples = []
            for line in prompt.splitlines():
                if not line.strip().startswith("{") or "|" in line:
                    continue
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.assertTrue(examples)
            for example in examples:
                with self.subTest(node=node_id, example=example):
                    Draft202012Validator(schema).validate(example)

    def test_validation_adapts_objects_without_changing_business_result(self):
        samples = {
            "business": {"business": "BN", "mentioned_businesses": ["BN"],
                         "clarification": "", "intent": "shopping", "business_requests": []},
            "extract": {"changes": [], "question_changes": [], "clarification": "",
                        "reason": "", "context_update": None},
            "shortlist": {"action": "recommend", "selected_plan_codes": [],
                          "follow_up": None, "reason": ""},
            "select": {"selected_plan_codes": [], "assessments": [], "reason": ""},
        }
        for node_id, sample in samples.items():
            with self.subTest(node=node_id):
                data = self.nodes[node_id + "_validation"]["data"]
                namespace = {}
                exec(compile(data["code"], node_id, "exec"), namespace)
                seen = []
                result = dict(ok=True, mode="ok", text="", error="", bundle={"mode": "ok", "instruction": ""})
                namespace["validate"] = lambda *args: seen.append(args) or result
                args = [sample if v["variable"] == "llm_output" else {} for v in data["variables"]]
                actual = namespace["main"](**dict(zip((v["variable"] for v in data["variables"]), args)))
                self.assertEqual(json.loads(actual["text"]), sample)
                self.assertIn(actual["text"], seen[0])

    def test_reply_remains_streaming_text(self):
        reply = self.nodes["reply"]["data"]
        self.assertNotEqual(reply["model"]["completion_params"].get("response_format"), "json_object")
        self.assertFalse(reply.get("structured_output_enabled", False))
        self.assertEqual(self.nodes["answer"]["data"]["answer"], "{{#reply.text#}}")


if __name__ == "__main__":
    unittest.main()
