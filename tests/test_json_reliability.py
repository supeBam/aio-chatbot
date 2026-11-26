import copy
import json
import unittest

from dify.measure_json_outputs import classify_attempt, summarize


VALID_EXTRACT = {
    "changes": [], "question_changes": [], "clarification": "", "reason": "",
    "context_update": None, "turn_intent": "shop", "link_request": "",
    "delivery_goal": "selection", "selection_context": "new", "replaced_temporary_ids": [],
    "availability_scope": "current_purchase", "turn_conditions": [],
    "temporarily_excluded_ids": [], "purchase_switch": None,
}


class JsonFailureMeasurementTests(unittest.TestCase):
    def test_valid_contracts_and_type_errors_are_measured_separately_from_semantics(self):
        valid = {
            "business": {"business": "BN", "mentioned_businesses": ["BN"], "intent": "shopping",
                         "clarification": "", "business_requests": []},
            "extract": VALID_EXTRACT,
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
            {**VALID_EXTRACT, "changes": "not an array"},
            {**VALID_EXTRACT, "context_update": 1},
        ):
            self.assertEqual(classify_attempt({"text": json.dumps(invalid)}, "extract"), "schema")

    def record(self, case="one", variant="schema", attempts=None):
        return {
            "case_id": case, "variant": variant, "node_id": "extract",
            "attempts": attempts if attempts is not None else [{
                "text": json.dumps(VALID_EXTRACT),
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
