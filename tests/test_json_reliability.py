import copy
import json
import unittest

from jsonschema import Draft202012Validator

from dify.build_multi_business_workflow import build
from dify.measure_json_outputs import classify_attempt, summarize
from dify.model_output_schemas import MODEL_SCHEMAS


class StructuredOutputContractTests(unittest.TestCase):
    def test_deepseek_internal_nodes_request_native_json_format(self):
        for enabled in (False, True):
            nodes = {node["id"]: node["data"]
                     for node in build(structured_outputs=enabled)["workflow"]["graph"]["nodes"]}
            for node_id in MODEL_SCHEMAS:
                with self.subTest(schema=enabled, node=node_id):
                    self.assertEqual(nodes[node_id]["model"]["completion_params"].get("response_format"),
                                     "json_object")
            self.assertNotEqual(nodes["reply"]["model"]["completion_params"].get("response_format"),
                                "json_object")

    def test_dify_llm_success_handle_reaches_validation_and_loop_update(self):
        graph = build()["workflow"]["graph"]
        for model_id in MODEL_SCHEMAS:
            with self.subTest(model=model_id):
                reachable = {model_id}
                pending = [model_id]
                while pending:
                    source = pending.pop()
                    for edge in graph["edges"]:
                        if edge["source"] != source:
                            continue
                        expected_handle = "source"
                        if edge["sourceHandle"] != expected_handle:
                            continue
                        if edge["target"] not in reachable:
                            reachable.add(edge["target"])
                            pending.append(edge["target"])
                self.assertIn(model_id + "_validation", reachable,
                              "Dify emits source on LLM success; missing it leaves current_text empty")
                self.assertIn(model_id + "_retry_update", reachable)

    def test_schemas_are_valid_and_internal_nodes_enable_them(self):
        nodes = {node["id"]: node["data"] for node in build()["workflow"]["graph"]["nodes"]}
        for node_id, schema in MODEL_SCHEMAS.items():
            with self.subTest(node=node_id):
                Draft202012Validator.check_schema(schema)
                self.assertTrue(nodes[node_id]["structured_output_enabled"])
                self.assertEqual(nodes[node_id]["structured_output"]["schema"], schema)
                self.assertEqual(nodes[node_id]["error_strategy"], "fail-branch")
                self.assertIn(node_id + "_node_failure", nodes)
        self.assertFalse(nodes["reply"]["structured_output_enabled"])
        self.assertNotIn("structured_output", nodes["reply"])
        self.assertEqual(nodes["answer"]["answer"], "{{#reply.text#}}")

    def test_baseline_switch_only_removes_schemas_not_failure_handling(self):
        baseline = build(structured_outputs=False)
        nodes = {node["id"]: node["data"] for node in baseline["workflow"]["graph"]["nodes"]}
        for node_id in MODEL_SCHEMAS:
            self.assertFalse(nodes[node_id]["structured_output_enabled"])
            self.assertNotIn("structured_output", nodes[node_id])
            self.assertIn(node_id + "_node_failure", nodes)
            self.assertEqual(nodes[node_id]["error_strategy"], "fail-branch")
        with self.assertRaises(ValueError):
            build(structured_outputs="false")
        treatment = build()
        for node in treatment["workflow"]["graph"]["nodes"]:
            if node["id"] in MODEL_SCHEMAS:
                node["data"].pop("structured_output")
                node["data"]["structured_output_enabled"] = False
        self.assertEqual(treatment, baseline)

    def test_literal_prompt_examples_match_their_schema(self):
        nodes = {node["id"]: node["data"] for node in build()["workflow"]["graph"]["nodes"]}
        for node_id, schema in MODEL_SCHEMAS.items():
            examples = []
            for line in nodes[node_id]["prompt_template"][0]["text"].splitlines():
                if line.strip().startswith("{") and "|" not in line:
                    examples.append(json.loads(line))
            self.assertTrue(examples, node_id)
            for example in examples:
                with self.subTest(node=node_id, example=example):
                    Draft202012Validator(schema).validate(example)

    def test_valid_contracts_and_type_errors_are_measured_separately_from_semantics(self):
        valid = {
            "business": {"business": "BN", "mentioned_businesses": ["BN"], "intent": "shopping",
                         "clarification": "", "business_requests": []},
            "extract": {"changes": [], "question_changes": [], "clarification": "", "reason": "", "context_update": None},
            "shortlist": {"action": "recommend", "selected_plan_codes": ["UNKNOWN_BUT_STRING"],
                          "follow_up": None, "reason": "compare"},
            "select": {"assessments": [], "reason": "compare"},
        }
        for node_id, output in valid.items():
            self.assertEqual(classify_attempt({"text": json.dumps(output)}, node_id), "valid")
            invalid = {**output, "unexpected": True}
            self.assertEqual(classify_attempt({"text": json.dumps(invalid)}, node_id), "schema")
            self.assertEqual(classify_attempt({"text": "```json\n{}\n```"}, node_id), "syntax")
        for invalid in (
            {"changes": "not an array", "question_changes": [], "clarification": "", "reason": "", "context_update": None},
            {"changes": [], "question_changes": [], "clarification": "", "reason": "", "context_update": 1},
        ):
            self.assertEqual(classify_attempt({"text": json.dumps(invalid)}, "extract"), "schema")


class JsonFailureMeasurementTests(unittest.TestCase):
    def record(self, case="one", variant="schema", attempts=None):
        return {
            "case_id": case, "variant": variant, "node_id": "extract",
            "attempts": attempts if attempts is not None else [{
                "text": '{"changes":[],"question_changes":[],"clarification":"","reason":"","context_update":null}',
            }],
        }

    def test_first_and_after_retry_rates_include_provider_failures_without_hiding_them(self):
        good = self.record()["attempts"][0]
        result = summarize([
            self.record(case="one", variant="baseline", attempts=[{"text": "truncated"}, good]),
            self.record(case="one", attempts=[good]),
            self.record(case="two", attempts=[{"node_error": {"kind": "schema"}}, good]),
            self.record(case="three", attempts=[{"node_error": {"kind": "transport"}}]),
        ])
        self.assertEqual(result["baseline"]["first_attempt"]["json_failure_rate"], 1)
        self.assertEqual(result["baseline"]["after_retries"]["json_failure_rate"], 0)
        self.assertEqual(result["schema"]["first_attempt"]["schema"], 1)
        self.assertEqual(result["schema"]["after_retries"]["json_failure_rate"], 0)
        self.assertEqual(result["schema"]["after_retries"]["transport"], 1)
        self.assertAlmostEqual(result["schema"]["after_retries"]["all_failure_rate"], 1 / 3)
        self.assertIsNone(result["schema"]["after_retries"]["zero_failure_95pct_upper_bound"])
        self.assertEqual(result["paired_cases"], 1)
        self.assertEqual(result["paired_turns"], 1)
        self.assertEqual(result["unpaired_cases"]["schema"], 2)

    def test_turn_metrics_count_a_multistage_request_once(self):
        extraction = self.record()
        routing = {**extraction, "node_id": "business", "attempts": [{"text": "{}"}]}
        result = summarize([routing, extraction])
        self.assertEqual(result["schema"]["after_retries"]["observations"], 2)
        self.assertEqual(result["schema:turn"]["after_retries"]["observations"], 1)
        self.assertEqual(result["schema:turn"]["after_retries"]["json_failure_rate"], 1)

    def test_one_percent_threshold_is_exact_and_small_samples_are_not_proof(self):
        good = self.record()["attempts"][0]
        records = [self.record(str(index)) for index in range(100)]
        records[0]["attempts"] = [{"text": "{}"}]
        result = summarize(records)["schema"]["after_retries"]
        self.assertEqual(result["json_failure_rate"], 0.01)
        self.assertTrue(result["observed_json_target_met"])
        records[1]["attempts"] = [{"text": "{}"}]
        self.assertFalse(summarize(records)["schema"]["after_retries"]["observed_json_target_met"])
        small = summarize([self.record()])["schema"]["after_retries"]
        self.assertGreater(small["zero_failure_95pct_upper_bound"], 0.01)
        large = summarize([self.record(str(index)) for index in range(300)])["schema"]["after_retries"]
        self.assertLess(large["zero_failure_95pct_upper_bound"], 0.01)
        self.assertEqual(classify_attempt(good, "extract"), "valid")

    def test_missing_or_duplicate_measurements_fail_explicitly(self):
        for records in (
            [], [self.record(), self.record()],
            [self.record(attempts=[])],
            [self.record(attempts=[{"node_error": {"kind": "unknown"}}])],
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                summarize(copy.deepcopy(records))


if __name__ == "__main__":
    unittest.main()
