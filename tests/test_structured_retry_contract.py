import copy
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from dify.model_output_schemas import MODEL_SCHEMAS


WORKFLOW = Path(__file__).resolve().parents[1] / "dify" / "AIO Multi Business Structured.txt"


def assert_dify_depth(test, value, depth=1, path="output"):
    if depth > 5:
        test.fail(f"Depth limit 5 reached, object too deep: {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, dict):
                assert_dify_depth(test, child, depth + 1, f"{path}.{key}")
            elif isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, (dict, list)):
                        assert_dify_depth(test, item, depth + 1, f"{path}.{key}[{index}]")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (dict, list)):
                assert_dify_depth(test, item, depth + 1, f"{path}[{index}]")


class StructuredRetryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node["data"] for node in cls.flow["workflow"]["graph"]["nodes"]}

    def test_followup_with_saved_selection_stays_within_dify_depth_limit(self):
        def run(node_id, *args, **kwargs):
            namespace = {}
            exec(compile(self.nodes[node_id]["code"], node_id, "exec"), namespace)
            result = namespace["main"](*args, **kwargs)
            assert_dify_depth(self, result)
            return result

        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["current_business"] = "MS"
        needs = snapshot["ms_needs"]
        needs["user_context"] = "移动通讯"
        needs["last_recommended_codes"] = ["P1", "P2"]
        temporary = {"id": "T1", "text": "移动通讯", "attitude": "interested",
                     "priority": "required", "evidence": ["移动通讯"]}
        needs["active_selection"] = {
            "shown_plan_codes": ["P1", "P2"], "turn_intent": "availability",
            "availability_scope": "current_purchase", "turn_requirements": [temporary],
            "temporarily_excluded_ids": [],
        }
        ingress = run("ingress", "还有其他的选择吗？", "message", "", **snapshot)
        self.assertTrue(ingress["ok"], ingress["error"])
        bundle = {"snapshot": ingress["snapshot"], "business": "MS", "mode": "analyze",
                  "instruction": "", "excluded_plan_codes": ["P1", "P2"],
                  "turn_intent": "availability", "turn_requirements": [temporary],
                  "persist_reply": True, "interview_topic": "", "selected_plans": []}
        plans = [{"plan_code": code, "tag": ["移动通讯"]} for code in ("P1", "P2", "P3")]
        choice = {"action": "recommend", "selected_plan_codes": ["P3"],
                  "follow_up": None, "reason": "其他选择"}
        validation = run("shortlist_validation", json.dumps(bundle), plans, choice)
        self.assertFalse(validation["retry"], validation["error"])
        decision = run("product_decision", json.dumps(bundle), plans, validation["text"])
        self.assertEqual(decision["bundle"]["mode"], "availability")
        self.assertEqual(json.loads(decision["shortlist_json"])["selected_plan_codes"], ["P3"])

    def test_existing_purchase_archive_is_migrated_without_losing_requirements(self):
        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["current_business"] = "MS"
        previous = copy.deepcopy(snapshot["ms_needs"])
        previous["purchase_label"] = "原有选购"
        previous["requirements"] = [
            {"id": "R1", "text": "月费150内", "attitude": "interested",
             "priority": "required", "evidence": ["150内"]}
        ]
        previous["next_requirement_id"] = 2
        snapshot["ms_needs"]["purchase_archive"] = {"S1": previous}
        namespace = {}
        exec(compile(self.nodes["ingress"]["code"], "ingress", "exec"), namespace)
        result = namespace["main"]("继续选购", "message", "", **snapshot)
        self.assertTrue(result["ok"], result["error"])
        assert_dify_depth(self, result)
        self.assertEqual(json.loads(result["snapshot"]["ms_needs"]["purchase_archive"])["S1"], previous)

    def test_recommendation_then_alternatives_preserves_state_across_nodes(self):
        def run(node_id, *args, **kwargs):
            namespace = {}
            exec(compile(self.nodes[node_id]["code"], node_id, "exec"), namespace)
            result = namespace["main"](*args, **kwargs)
            assert_dify_depth(self, result)
            return result

        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["current_business"] = "MS"
        plans = run("fetch_tags", "MS", snapshot)["bundle"]["plans_tags"]
        codes = [plan["plan_code"] for plan in plans[:3]]
        self.assertEqual(len(codes), 3)
        snapshot.update(ms_catalog_status="ready", ms_plans_tags=plans)
        requirement = {"id": "T1", "text": "移动通讯", "attitude": "interested",
                       "priority": "required", "evidence": ["移动通讯"]}
        first = {"snapshot": snapshot, "business": "MS", "mode": "availability",
                 "instruction": "", "persist_reply": True, "interview_topic": "",
                 "delivery_goal": "selection", "turn_intent": "availability",
                 "availability_scope": "current_purchase", "turn_requirements": [requirement],
                 "selected_plans": [{"plan_code": code, "markdown": "方案名称：移动通讯"}
                                    for code in codes[:2]]}
        context = run("reply_context", first, "有冇咩移動通訊Plan推介？")
        self.assertEqual(context["presentation"]["plan_codes"], codes[:2])
        state = context["snapshot"]["ms_needs"]
        self.assertEqual(state["selection_turn_requirements"], [requirement])
        self.assertNotIn("turn_requirements", state["active_selection"])
        recorded = run("record_reply", context["snapshot"], "MS", context["mode"], "推荐",
                       context["selected_plans_json"], True, "", context["presentation"], context["plan_summary"])
        message = "还有其他的选择吗？"
        ingress = run("ingress", message, "message", "", **recorded["snapshot"])
        self.assertTrue(ingress["ok"], ingress["error"])
        route = {"business": "MS", "mentioned_businesses": ["MS"], "clarification": "",
                 "intent": "shopping", "business_requests": []}
        prepared = run("prepare", json.dumps(route), ingress["snapshot"], "message", message)
        self.assertTrue(prepared["ok"], prepared["text"])
        catalog = run("catalog", {"status": "ready", "plans_tags": plans, "request_json": "", "error": ""},
                      prepared["snapshot"], "MS")
        self.assertEqual(json.loads(catalog["needs_json"])["active_selection"]["turn_requirements"], [requirement])
        extraction = {
            "changes": [], "question_changes": [], "clarification": "", "reason": "其他选择",
            "context_update": None, "turn_intent": "availability", "link_request": "",
            "delivery_goal": "selection", "selection_context": "alternatives", "replaced_temporary_ids": [],
            "availability_scope": "current_purchase", "turn_conditions": [],
            "temporarily_excluded_ids": [], "purchase_switch": None,
        }
        checked = run("extract_validation", extraction, catalog["snapshot"], "MS", "message")
        self.assertFalse(checked["retry"], checked["error"])
        merged = run("merge", checked["text"], catalog["snapshot"], "MS", "message")
        bundle = json.loads(merged["bundle"])
        self.assertEqual(bundle["turn_requirements"], [requirement])
        self.assertEqual(bundle["excluded_plan_codes"], codes[:2])
        state = run("decision_state", merged["bundle"])
        choice = {"action": "recommend", "selected_plan_codes": [codes[2]],
                  "follow_up": None, "reason": "其他选择"}
        validation = run("shortlist_validation", merged["bundle"], plans, choice)
        self.assertFalse(validation["retry"], validation["error"])
        decision = run("product_decision", merged["bundle"], plans, validation["text"])
        run("product_state", decision["bundle"])
        ranked = run("rank", decision["bundle"], plans, decision["shortlist_json"])
        self.assertEqual(ranked["error"], "")
        candidate = json.loads(ranked["sales_candidates_json"])[0]
        quote = next(line for line in candidate["markdown"].splitlines() if line.strip())
        selection = {"reason": "核对其他计划", "assessments": [{
            "plan_code": codes[2], "link_label": "", "info_answers": [],
            "requirements": [{"requirement_id": "T1", "status": "supported",
                              "quote": quote, "explanation": "移动通讯计划"}],
        }]}
        selected = run("validate_selection", json.dumps(selection), ranked["sales_candidates_json"], state["needs_json"])
        self.assertEqual(selected["error"], "")
        recommendation = run("recommendation", decision["bundle"], selected["recommendations_json"], "",
                             ranked["error"], ranked["sales_candidates_json"])
        second = run("reply_context", recommendation["bundle"], message)
        self.assertEqual(second["presentation"]["plan_codes"], [codes[2]])
        self.assertEqual(second["snapshot"]["ms_needs"]["active_selection"]["shown_plan_codes"], codes)
        saved = run("record_reply", second["snapshot"], "MS", second["mode"], "其他选择",
                    second["selected_plans_json"], True, "", second["presentation"], second["plan_summary"])
        self.assertEqual(saved["snapshot"]["ms_needs"]["selection_turn_requirements"], [requirement])

    def test_purchase_switch_archive_survives_node_depth_validation(self):
        def run(node_id, *args, **kwargs):
            namespace = {}
            exec(compile(self.nodes[node_id]["code"], node_id, "exec"), namespace)
            result = namespace["main"](*args, **kwargs)
            assert_dify_depth(self, result)
            return result

        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["current_business"] = "MS"
        original = {"id": "R1", "text": "每月150内", "priority": "required",
                    "attitude": "interested", "evidence": ["150内"]}
        snapshot["ms_needs"].update(requirements=[original], next_requirement_id=2)
        message = "另外幫屋企人開張卡，要50GB"
        snapshot["ms_needs"]["pending_messages"] = [message]
        changes = {
            "changes": [{"business": "MS", "operation": "add", "requirement_id": "", "text": "50GB",
                         "attitude": "interested", "priority": "required", "evidence": ["要50GB"]}],
            "question_changes": [], "clarification": "", "reason": "", "context_update": None,
            "turn_intent": "shop", "turn_conditions": [], "temporarily_excluded_ids": [],
            "purchase_switch": {"target": "new", "evidence": [message]},
        }
        validated = run("extract_validation", changes, snapshot, "MS")
        self.assertFalse(validated["retry"], validated["error"])
        family = json.loads(run("merge", validated["text"], snapshot, "MS")["bundle"])
        family_snapshot = family["snapshot"]
        self.assertEqual(json.loads(family_snapshot["ms_needs"]["purchase_archive"])["S1"]["requirements"], [original])
        ingress = run("ingress", "返去我嗰張", "message", "", **family_snapshot)
        self.assertTrue(ingress["ok"], ingress["error"])
        switched_snapshot = ingress["snapshot"]
        switched_snapshot["ms_needs"]["pending_messages"] = ["返去我嗰張"]
        changes.update(changes=[], purchase_switch={"target": "S1", "evidence": ["我嗰張"]})
        validated = run("extract_validation", changes, switched_snapshot, "MS")
        self.assertFalse(validated["retry"], validated["error"])
        resumed = json.loads(run("merge", validated["text"], switched_snapshot, "MS")["bundle"])
        self.assertEqual(resumed["snapshot"]["ms_needs"]["requirements"], [original])
        self.assertEqual(
            json.loads(resumed["snapshot"]["ms_needs"]["purchase_archive"])["S2"]["requirements"][0]["text"],
            "50GB",
        )

    def test_address_change_clears_displayed_codes_in_flat_and_archived_state(self):
        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["current_business"] = "BN"
        previous = copy.deepcopy(snapshot["bn_needs"])
        previous.update(purchase_label="旧选购", last_reply="旧地址", last_recommended_codes=["OLD"])
        snapshot["bn_needs"].update(
            active_selection={"shown_plan_codes": ["OLD"], "turn_intent": "availability",
                              "availability_scope": "current_purchase", "turn_requirements": [],
                              "temporarily_excluded_ids": []},
            purchase_archive={"S1": previous},
        )
        namespace = {}
        exec(compile(self.nodes["ingress"]["code"], "ingress", "exec"), namespace)
        result = namespace["main"]("地址已变更", "address_change", "NEW", **snapshot)
        self.assertTrue(result["ok"], result["error"])
        assert_dify_depth(self, result)
        needs = result["snapshot"]["bn_needs"]
        self.assertEqual(needs["active_selection"]["shown_plan_codes"], [])
        self.assertEqual(json.loads(needs["purchase_archive"])["S1"]["last_recommended_codes"], [])
        self.assertEqual(result["snapshot"]["bn_scope_codes"], ["NEW"])

    def test_invalid_or_conflicting_selection_state_is_not_silently_discarded(self):
        namespace = {}
        exec(compile(self.nodes["ingress"]["code"], "ingress", "exec"), namespace)
        snapshot = {item["name"]: copy.deepcopy(item["value"])
                    for item in self.flow["workflow"]["conversation_variables"]}
        snapshot["ms_needs"]["purchase_archive"] = "{"
        with self.assertRaises(json.JSONDecodeError):
            namespace["main"]("查询", "message", "", **snapshot)
        del snapshot["ms_needs"]["purchase_archive"]
        snapshot["ms_needs"]["active_selection"] = {"turn_requirements": []}
        snapshot["ms_needs"]["selection_turn_requirements"] = []
        with self.assertRaisesRegex(ValueError, "临时需求重复"):
            namespace["main"]("查询", "message", "", **snapshot)

    def test_serialized_decision_bundle_reaches_all_branches(self):
        root = Path(__file__).resolve().parents[1]
        paths = [WORKFLOW]
        selected_workflow = root / "AIO Multi Business Structured.txt"
        if selected_workflow.is_file():
            paths.append(selected_workflow)
        for path in paths:
            with self.subTest(workflow=path.name, directory=path.parent.name):
                flow = yaml.safe_load(path.read_text(encoding="utf-8-sig"))["workflow"]
                nodes = {node["id"]: node["data"] for node in flow["graph"]["nodes"]}
                snapshot = {item["name"]: copy.deepcopy(item["value"]) for item in flow["conversation_variables"]}
                snapshot["ms_needs"]["user_context"] = "手机套餐"
                bundle = {"snapshot": snapshot, "business": "MS", "mode": "analyze", "instruction": ""}
                serialized = json.dumps(bundle, ensure_ascii=False)
                choice = {"action": "recommend", "selected_plan_codes": [], "follow_up": None, "reason": "没有候选"}
                for node_id in ("shortlist_validation", "product_decision", "skip_analysis"):
                    with self.subTest(node=node_id):
                        node = nodes[node_id]
                        variable = next(v for v in node["variables"] if v["variable"] == "bundle")
                        namespace = {}
                        exec(compile(node["code"], node_id, "exec"), namespace)
                        main = namespace["main"]
                        if node_id == "shortlist_validation":
                            args = ([], choice)
                        elif node_id == "product_decision":
                            args = ([], json.dumps(choice, ensure_ascii=False))
                        else:
                            args = ()
                        self.assertEqual(main(serialized, *args), main(bundle, *args))
                        self.assertEqual(variable["value_type"], "string")
                        with self.assertRaises(json.JSONDecodeError):
                            main("{", *args)

                plans = [{"plan_code": "P1", "tag": ["月费98元"]}]
                choices = [
                    ({"action": "recommend", "selected_plan_codes": ["P1"], "follow_up": None,
                      "reason": "核对套餐"}, "recommend"),
                    ({"action": "interview", "selected_plan_codes": [],
                      "follow_up": {"topic": "budget", "information_needed": "月费预算",
                                    "selection_impact": "影响套餐选择", "blocking": False},
                      "reason": "确认预算"}, "interview"),
                    ({"action": "recommend", "selected_plan_codes": ["UNKNOWN"], "follow_up": None,
                      "reason": "不在目录"}, "error"),
                ]
                scopes = {}
                for node_id in ("shortlist_validation", "product_decision"):
                    scopes[node_id] = {}
                    exec(compile(nodes[node_id]["code"], node_id, "exec"), scopes[node_id])
                for choice, expected_mode in choices:
                    with self.subTest(action=choice["action"], expected_mode=expected_mode):
                        validated = scopes["shortlist_validation"]["main"](serialized, plans, choice)
                        self.assertEqual(validated["retry"], expected_mode == "error")
                        result = scopes["product_decision"]["main"](serialized, plans, validated["text"])
                        self.assertEqual(result["bundle"]["mode"], expected_mode)

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
            "assessments": [{"plan_code": "MTOC30G98", "link_label": "", "requirements": [], "info_answers": []}],
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
