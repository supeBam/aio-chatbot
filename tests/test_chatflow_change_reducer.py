"""Regression tests for the Python Code nodes embedded in the Dify Chatflow."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "dify" / "AIO Demo.txt"


def load_code_node(node_id: str):
    workflow = yaml.safe_load(FLOW_PATH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in workflow["workflow"]["graph"]["nodes"]}
    namespace = {}
    code = nodes[node_id]["data"]["code"]
    exec(compile(code, f"{FLOW_PATH}#{node_id}", "exec"), namespace)
    return namespace


def empty_state():
    return {
        "schema_version": "2.1",
        "business_categories": [],
        "business_category_confirmed": False,
        "scan_tag": [],
        "plan_tags": [],
        "uninterested_plan_tags": [],
        "uninterested_scan_tag": [],
        "bandwidth_mbps": None,
        "monthly_fee": None,
        "contract_months": None,
    }


def change_set(**overrides):
    result = {
        "schema_version": "3.2",
        "category_proposal": {"present": False, "business_categories": [], "evidence": ""},
        "tag_changes": [],
        "constraint_changes": [],
        "assistant_message": "",
    }
    result.update(overrides)
    proposal = result["category_proposal"]
    if proposal is None:
        result["category_proposal"] = {"present": False, "business_categories": [], "evidence": ""}
    elif "present" not in proposal:
        result["category_proposal"] = {
            "present": True,
            "business_categories": proposal["business_categories"],
            "evidence": proposal["evidence"],
        }
    for item in result["tag_changes"]:
        if item["to"] is None:
            item["to"] = "remove"
    for item in result["constraint_changes"]:
        target = item.pop("to")
        if target is None:
            item.update({"action": "clear", "operator": "eq", "value": 0})
        else:
            item.update({"action": "set", "operator": target["operator"], "value": target["value"]})
    return result


class ChangeReducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = load_code_node("validate")

    def apply(self, changes, state, message):
        return self.node["main"](changes, json.dumps(state, ensure_ascii=False), message)

    def test_inferred_category_is_immediately_available_without_confirmation(self):
        changes = change_set(
            category_proposal={"business_categories": ["BN"], "evidence": "我想装宽频"},
        )

        result = self.apply(changes, empty_state(), "我想装宽频")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["business_categories"], ["BN"])
        self.assertTrue(state["business_category_confirmed"])
        self.assertEqual(json.loads(result["result_text"]), state)
        self.assertNotIn("这个业务类别正确吗", result["result_text"])

    def test_accepts_legacy_v31_output_without_requiring_category_confirmation(self):
        changes = change_set(
            category_proposal={"business_categories": ["BN"], "evidence": "宽频套餐"},
            tag_changes=[
                {"tag": "netflix", "to": "interested", "evidence": "必须有Netflix"},
                {"tag": "router_wifi7", "to": "interested", "evidence": "Wi-Fi 7最好"},
            ],
        )
        changes["schema_version"] = "3.1"
        changes["category_confirmation"] = {"decision": "none", "evidence": ""}

        result = self.apply(
            changes,
            empty_state(),
            "宽频套餐必须有Netflix，Wi-Fi 7最好",
        )
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["business_categories"], ["BN"])
        self.assertEqual(state["plan_tags"], ["netflix", "router_wifi7"])

    def test_follow_up_accumulates_tags_and_numeric_constraints_without_category_confirmation(self):
        previous = empty_state()
        previous["business_categories"] = ["BN"]
        changes = change_set(
            tag_changes=[{"tag": "gaming", "to": "interested", "evidence": "喜欢打机"}],
            constraint_changes=[
                {"field": "bandwidth_mbps", "to": {"operator": "gte", "value": 2500}, "evidence": "至少2.5G"},
                {"field": "monthly_fee", "to": {"operator": "lte", "value": 200}, "evidence": "月费不超过HK$200"},
                {"field": "contract_months", "to": {"operator": "eq", "value": 24}, "evidence": "24个月合约"},
            ],
        )

        result = self.apply(
            changes,
            previous,
            "对，我喜欢打机，至少2.5G，月费不超过HK$200，24个月合约",
        )
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertTrue(state["business_category_confirmed"])
        self.assertEqual(state["scan_tag"], ["gaming"])
        self.assertEqual(state["bandwidth_mbps"], {"operator": "gte", "value": 2500})
        self.assertEqual(state["monthly_fee"], {"operator": "lte", "value": 200})
        self.assertEqual(state["contract_months"], {"operator": "eq", "value": 24})
        self.assertEqual(json.loads(result["result_text"]), state)

    def test_numeric_follow_up_keeps_inferred_category_available(self):
        previous = empty_state()
        previous["business_categories"] = ["BN"]
        previous["scan_tag"] = ["streaming", "gaming"]
        changes = change_set(
            category_proposal={"business_categories": ["BN"], "evidence": "宽频速度2.5G"},
            constraint_changes=[
                {"field": "bandwidth_mbps", "to": {"operator": "eq", "value": 2500}, "evidence": "宽频速度2.5G"},
                {"field": "monthly_fee", "to": {"operator": "eq", "value": 200}, "evidence": "月费200"},
                {"field": "contract_months", "to": {"operator": "eq", "value": 24}, "evidence": "合约期24"},
            ],
        )

        result = self.apply(changes, previous, "宽频速度2.5G,月费200，合约期24")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertTrue(state["business_category_confirmed"])
        self.assertEqual(state["scan_tag"], ["streaming", "gaming"])
        self.assertEqual(state["bandwidth_mbps"], {"operator": "eq", "value": 2500})
        self.assertEqual(state["monthly_fee"], {"operator": "eq", "value": 200})
        self.assertEqual(state["contract_months"], {"operator": "eq", "value": 24})
        self.assertNotIn("这个业务类别正确吗", result["result_text"])

    def test_multi_turn_dialogue_preserves_needs_without_category_confirmation(self):
        state = empty_state()
        first_message = "喜欢看电影，但是预算优先，还喜欢打游戏，希望速度快一点"
        first = self.apply(
            change_set(
                tag_changes=[
                    {"tag": "streaming", "to": "interested", "evidence": "喜欢看电影"},
                    {"tag": "gaming", "to": "interested", "evidence": "喜欢打游戏"},
                ],
                assistant_message="你想了解家居宽频，还是单独订阅串流内容？速度也可以再给我具体数值。",
            ),
            state,
            first_message,
        )
        self.assertEqual(first["update_status"], "accepted")
        state = json.loads(first["conversation_state_json"])

        second_message = "家居宽频"
        second = self.apply(
            change_set(
                category_proposal={"business_categories": ["BN"], "evidence": "家居宽频"},
                assistant_message="你对宽频速度、月费或合约期有什么要求？",
            ),
            state,
            second_message,
        )
        self.assertEqual(second["update_status"], "accepted")
        state = json.loads(second["conversation_state_json"])

        third_message = "宽频速度2.5G,月费200，合约期24"
        third = self.apply(
            change_set(
                category_proposal={"business_categories": ["BN"], "evidence": "宽频速度2.5G"},
                constraint_changes=[
                    {"field": "bandwidth_mbps", "to": {"operator": "eq", "value": 2500}, "evidence": "宽频速度2.5G"},
                    {"field": "monthly_fee", "to": {"operator": "eq", "value": 200}, "evidence": "月费200"},
                    {"field": "contract_months", "to": {"operator": "eq", "value": 24}, "evidence": "合约期24"},
                ],
            ),
            state,
            third_message,
        )
        final_state = json.loads(third["conversation_state_json"])

        self.assertEqual(third["update_status"], "accepted")
        self.assertTrue(final_state["business_category_confirmed"])
        self.assertEqual(final_state["scan_tag"], ["streaming", "gaming"])
        self.assertEqual(final_state["plan_tags"], [])
        self.assertEqual(final_state["bandwidth_mbps"], {"operator": "eq", "value": 2500})
        self.assertEqual(final_state["monthly_fee"], {"operator": "eq", "value": 200})
        self.assertEqual(final_state["contract_months"], {"operator": "eq", "value": 24})
        self.assertNotIn("这个业务类别正确吗", third["result_text"])

    def test_plan_tags_move_between_interested_and_uninterested_lists(self):
        previous = empty_state()
        previous["plan_tags"] = ["netflix", "router_wifi7"]
        changes = change_set(
            tag_changes=[
                {"tag": "netflix", "to": "uninterested", "evidence": "不要Netflix"},
                {"tag": "disney_plus", "to": "interested", "evidence": "喜欢Disney+"},
            ]
        )

        result = self.apply(changes, previous, "不要Netflix，但喜欢Disney+")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["plan_tags"], ["router_wifi7", "disney_plus"])
        self.assertEqual(state["uninterested_plan_tags"], ["netflix"])

    def test_scene_tags_cannot_be_marked_as_uninterested_plan_tags(self):
        result = self.apply(
            change_set(tag_changes=[{"tag": "gaming", "to": "uninterested", "evidence": "不要打机"}]),
            empty_state(),
            "不要打机",
        )

        self.assertEqual(result["update_status"], "rejected")
        self.assertIn("Scene tags cannot be marked uninterested", result["validation_error"])
        self.assertEqual(json.loads(result["conversation_state_json"]), empty_state())

    def test_remove_clears_scene_and_plan_tags_from_their_own_fields(self):
        previous = empty_state()
        previous["scan_tag"] = ["gaming"]
        previous["plan_tags"] = ["netflix"]
        result = self.apply(
            change_set(
                tag_changes=[
                    {"tag": "gaming", "to": "remove", "evidence": "不考虑打机"},
                    {"tag": "netflix", "to": "remove", "evidence": "不考虑Netflix"},
                ]
            ),
            previous,
            "不考虑打机，也不考虑Netflix",
        )
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["scan_tag"], [])
        self.assertEqual(state["plan_tags"], [])
        self.assertEqual(state["uninterested_plan_tags"], [])

    def test_rejects_evidence_not_in_current_message_without_mutating_state(self):
        changes = change_set(
            tag_changes=[{"tag": "netflix", "to": "interested", "evidence": "喜欢Netflix"}],
        )

        result = self.apply(changes, empty_state(), "我想要更快的宽频")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "rejected")
        self.assertIn("evidence", result["validation_error"])
        self.assertEqual(state, empty_state())

    def test_same_turn_category_inference_keeps_other_updates(self):
        changes = change_set(
            category_proposal={"business_categories": ["OTT"], "evidence": "我想看Netflix"},
            constraint_changes=[
                {"field": "monthly_fee", "to": {"operator": "lte", "value": 100}, "evidence": "月费不超过100"}
            ],
        )

        result = self.apply(changes, empty_state(), "我想看Netflix，月费不超过100")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["business_categories"], ["OTT"])
        self.assertTrue(state["business_category_confirmed"])
        self.assertEqual(state["monthly_fee"], {"operator": "lte", "value": 100})

    def test_legacy_state_category_is_available_after_migration(self):
        legacy = {
            "schema_version": "1.2",
            "requirement_state": {
                "requested_services": ["BN"],
                "requirements_by_service": {
                    "BN": {
                        "required": {
                            "bandwidth_mbps": {"operator": "gte", "value": 2500},
                            "contract_months": None,
                            "monthly_fee": None,
                            "bundled_services": {"required_all": ["netflix"], "required_any": []},
                        },
                        "interested": {"bundled_services": ["netflix"], "scene_tags": ["gaming"]},
                        "refused": {"bundled_services": []},
                    },
                    "OTT": {},
                },
            },
            "clarification_state": {"pending": [], "resolved_ids": []},
        }
        result = self.apply(
            change_set(),
            legacy,
            "我继续补充需求",
        )
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["business_categories"], ["BN"])
        self.assertTrue(state["business_category_confirmed"])
        self.assertEqual(state["plan_tags"], ["netflix"])
        self.assertEqual(state["scan_tag"], ["gaming"])
        self.assertEqual(state["bandwidth_mbps"], {"operator": "gte", "value": 2500})

    def test_v20_state_migration_splits_plan_scan_and_negative_tags(self):
        previous = {
            "schema_version": "2.0",
            "business_categories": ["BN"],
            "business_category_confirmed": True,
            "interested_tags": ["gaming", "netflix"],
            "uninterested_tags": ["streaming", "disney_plus"],
            "bandwidth_mbps": {"operator": "eq", "value": 2000},
            "monthly_fee": None,
            "contract_months": None,
        }

        result = self.apply(change_set(), previous, "继续")
        state = json.loads(result["conversation_state_json"])

        self.assertEqual(result["update_status"], "accepted")
        self.assertEqual(state["scan_tag"], ["gaming"])
        self.assertEqual(state["plan_tags"], ["netflix"])
        self.assertEqual(state["uninterested_plan_tags"], ["disney_plus"])
        self.assertEqual(state["uninterested_scan_tag"], ["streaming"])
        self.assertEqual(state["bandwidth_mbps"], {"operator": "eq", "value": 2000})

    def test_extraction_prompt_defines_categories_and_separate_tag_state(self):
        workflow = yaml.safe_load(FLOW_PATH.read_text(encoding="utf-8"))
        nodes = {node["id"]: node for node in workflow["workflow"]["graph"]["nodes"]}
        extract_node = nodes["extract"]["data"]
        system_prompt = extract_node["prompt_template"][0]["text"]

        self.assertIn("BN：家居固定宽频", system_prompt)
        self.assertIn("OTT：独立订阅", system_prompt)
        self.assertIn("MS：流动通讯服务", system_prompt)
        self.assertIn("不要让用户确认你对类别的判断", system_prompt)
        self.assertIn("scan_tag", system_prompt)
        self.assertIn("plan_tags", system_prompt)
        self.assertIn("uninterested_plan_tags", system_prompt)
        self.assertTrue(extract_node["structured_output_enabled"])
        self.assertIn("structured_output", extract_node)
        self.assertEqual(
            extract_node["structured_output"]["schema"]["required"],
            [
                "schema_version",
                "category_proposal",
                "tag_changes",
                "constraint_changes",
                "assistant_message",
            ],
        )
        self.assertNotIn("category_confirmation", extract_node["structured_output"]["schema"]["properties"])
        validate_inputs = nodes["validate"]["data"]["variables"]
        llm_input = next(item for item in validate_inputs if item["variable"] == "llm_output")
        self.assertEqual(llm_input["value_selector"], ["extract", "structured_output"])
        self.assertEqual(llm_input["value_type"], "object")


class RecommendationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = load_code_node("assemble")

    def assemble(self, state, recommendation_output, plan_infos):
        return self.node["main"](
            json.dumps(state, ensure_ascii=False),
            recommendation_output,
            plan_infos,
            "",
            "",
            "accepted",
            "",
        )

    def test_accepts_exact_candidate_codes_and_returns_recommendations(self):
        state = empty_state()
        state["business_categories"] = ["BN"]
        state["business_category_confirmed"] = True
        candidates = json.dumps(
            {
                "plans": [
                    {"plan_code": "BN-A", "product_intro": "2.5G broadband"},
                    {"plan_code": "BN-B", "product_intro": "1G broadband"},
                ]
            }
        )
        result = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": [{"plan_code": "BN-A", "description": "符合速度需求"}]}),
                candidates,
            )["result_text"]
        )

        self.assertEqual(result["recommendation_status"], "accepted")
        self.assertEqual(result["recommendations"], [{"plan_code": "BN-A", "description": "符合速度需求"}])

    def test_accepts_plan_code_from_the_supplied_bn_summary_shape(self):
        state = empty_state()
        state["business_categories"] = ["BN"]
        state["business_category_confirmed"] = True
        plans_path = ROOT / "data" / "bn_plan_summaries.json"
        backend_data = json.loads(plans_path.read_text(encoding="utf-8"))
        candidate_code = backend_data["plans"][0]["plan_code"]
        result = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": [{"plan_code": candidate_code, "description": "来自实际候选"}]}),
                json.dumps(backend_data, ensure_ascii=False),
            )["result_text"]
        )

        self.assertEqual(result["recommendation_status"], "accepted")
        self.assertEqual(result["recommendations"][0]["plan_code"], candidate_code)

    def test_rejects_altered_or_duplicate_plan_codes(self):
        state = empty_state()
        state["business_categories"] = ["BN"]
        state["business_category_confirmed"] = True
        candidates = json.dumps({"plans": [{"plan_code": "BN-A"}]})

        altered = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": [{"plan_code": "bn-a", "description": "wrong case"}]}),
                candidates,
            )["result_text"]
        )
        duplicate = json.loads(
            self.assemble(
                state,
                json.dumps(
                    {
                        "recommendations": [
                            {"plan_code": "BN-A", "description": "first"},
                            {"plan_code": "BN-A", "description": "duplicate"},
                        ]
                    }
                ),
                candidates,
            )["result_text"]
        )

        self.assertEqual(altered["recommendation_status"], "rejected")
        self.assertEqual(altered["recommendations"], [])
        self.assertEqual(duplicate["recommendation_status"], "rejected")
        self.assertEqual(duplicate["recommendations"], [])

    def test_recommends_as_soon_as_category_is_inferred(self):
        state = empty_state()
        state["business_categories"] = ["BN"]
        result = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": [{"plan_code": "BN-A", "description": "candidate"}]}),
                json.dumps({"plans": [{"plan_code": "BN-A"}]}),
            )["result_text"]
        )

        self.assertEqual(result["recommendation_status"], "accepted")
        self.assertEqual(result["recommendations"], [{"plan_code": "BN-A", "description": "candidate"}])

    def test_does_not_recommend_when_category_remains_unresolved(self):
        state = empty_state()
        result = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": [{"plan_code": "BN-A", "description": "candidate"}]}),
                json.dumps({"plans": [{"plan_code": "BN-A"}]}),
            )["result_text"]
        )

        self.assertEqual(result["recommendation_status"], "category_unresolved")
        self.assertEqual(result["recommendations"], [])

    def test_rejects_more_than_three_recommendations(self):
        state = empty_state()
        state["business_categories"] = ["BN"]
        state["business_category_confirmed"] = True
        plans = [{"plan_code": f"BN-{index}"} for index in range(4)]
        recommendations = [
            {"plan_code": f"BN-{index}", "description": "valid"}
            for index in range(4)
        ]
        result = json.loads(
            self.assemble(
                state,
                json.dumps({"recommendations": recommendations}),
                json.dumps({"plans": plans}),
            )["result_text"]
        )

        self.assertEqual(result["recommendation_status"], "rejected")
        self.assertEqual(result["recommendations"], [])

    def test_chatflow_routes_empty_plan_input_to_clarification_or_query_response(self):
        workflow = yaml.safe_load(FLOW_PATH.read_text(encoding="utf-8"))
        graph = workflow["workflow"]["graph"]
        edges = graph["edges"]
        true_edge = next(edge for edge in edges if edge["source"] == "1792461459228" and edge["sourceHandle"] == "true")
        false_edge = next(edge for edge in edges if edge["source"] == "1792461459228" and edge["sourceHandle"] == "false")
        nodes = {node["id"]: node for node in graph["nodes"]}

        self.assertEqual(nodes[true_edge["target"]]["data"]["answer"], "{{#validate.result_text#}}")
        self.assertEqual(false_edge["target"], "recommend")
        self.assertTrue(nodes["recommend"]["data"]["memory"]["window"]["enabled"])
        self.assertTrue(nodes["recommend"]["data"]["structured_output_enabled"])
        assemble_inputs = nodes["assemble"]["data"]["variables"]
        recommendation_input = next(item for item in assemble_inputs if item["variable"] == "recommendation_output")
        self.assertEqual(recommendation_input["value_selector"], ["recommend", "structured_output"])
        self.assertEqual(recommendation_input["value_type"], "object")


if __name__ == "__main__":
    unittest.main()
