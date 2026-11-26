import json
import unittest

import yaml
from jsonschema import Draft202012Validator

from dify.build_structured_variant import DESTINATION, SOURCE, build
from dify import workflow_logic
from dify.model_output_schemas import MODEL_SCHEMAS


class StructuredVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = yaml.safe_load(DESTINATION.read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node for node in cls.flow["workflow"]["graph"]["nodes"]}
        cls.edges = cls.flow["workflow"]["graph"]["edges"]

    def test_importable_exports_are_identical(self):
        self.assertEqual(DESTINATION.with_suffix(".txt").read_bytes(), DESTINATION.read_bytes())

    def test_artifact_is_reproducible(self):
        original = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(self.flow, build(original))
        self.assertEqual(
            {item["name"]: item["value"] for item in self.flow["workflow"]["conversation_variables"]},
            workflow_logic.DEFAULTS,
        )

    def test_schema_models_and_success_failure_paths(self):
        for node_id, schema in MODEL_SCHEMAS.items():
            with self.subTest(node=node_id):
                Draft202012Validator.check_schema(schema)
                llm = self.nodes[node_id]["data"]
                if node_id != "business":
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
                self.assertIn((node_id + "_validation", "source", node_id + "_retry_update"), edge_pairs)
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

    def prompt_examples(self, node_id):
        prompt = self.nodes[node_id]["data"]["prompt_template"][0]["text"]
        return [
            json.loads(line)
            for line in prompt.splitlines()
            if line.strip().startswith("{") and "|" not in line
        ]

    def test_examples_follow_schemas(self):
        for node_id, schema in MODEL_SCHEMAS.items():
            examples = self.prompt_examples(node_id)
            self.assertTrue(examples, node_id)
            for example in examples:
                with self.subTest(node=node_id, example=example):
                    Draft202012Validator(schema).validate(example)

    def test_validation_adapts_objects_without_changing_business_result(self):
        for node_id in MODEL_SCHEMAS:
            sample = self.prompt_examples(node_id)[0]
            with self.subTest(node=node_id):
                data = self.nodes[node_id + "_validation"]["data"]
                namespace = {}
                exec(compile(data["code"], node_id, "exec"), namespace)
                seen = []
                result = dict(ok=True, mode="ok", text="", error="", bundle={"mode": "ok", "instruction": ""})
                handler = {"business": "prepare", "extract": "merge",
                           "shortlist": "product_decision", "select": "validate_selection"}[node_id]
                namespace[handler] = lambda *args: seen.append(args) or result
                values = {"llm_output": sample,
                          "bundle": workflow_logic.pack_bundle({"snapshot": workflow_logic.DEFAULTS, "mode": "ok"})}
                args = [values.get(v["variable"], {}) for v in data["variables"]]
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
