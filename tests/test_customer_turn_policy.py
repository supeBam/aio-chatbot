import copy
import json
from pathlib import Path
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / "dify" / "AIO Multi Business Structured.yml"


class CustomerTurnPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        graph = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["workflow"]
        cls.nodes = {node["id"]: node["data"] for node in graph["graph"]["nodes"]}
        cls.defaults = {variable["name"]: variable["value"] for variable in graph["conversation_variables"]}
        cls.run_node = {}
        for node_id, data in cls.nodes.items():
            if data["type"] == "code":
                scope = {}
                exec(compile(data["code"], node_id, "exec"), scope)
                cls.run_node[node_id] = scope["main"]

    def setUp(self):
        self.snapshot = copy.deepcopy(self.defaults)

    def merge(self, message, **decisions):
        self.snapshot["ms_needs"]["pending_messages"] = [message]
        result = {
            "changes": [], "question_changes": [], "clarification": "", "reason": "",
            "context_update": None, "turn_intent": "shop", "turn_conditions": [], "purchase_switch": None,
            "temporarily_excluded_ids": [],
            **decisions,
        }
        validated = self.run_node["extract_validation"](result, self.snapshot, "MS")
        self.assertFalse(validated["retry"], validated["error"])
        return self.run_node["merge"](validated["text"], self.snapshot, "MS")["bundle"]

    def test_unrelated_turn_leaves_purchase_and_routing_memory_untouched(self):
        self.snapshot["current_business"] = "MS"
        self.snapshot["ms_needs"]["last_reply"] = "你每月大概用幾多GB？"
        self.snapshot["routing_context"]["messages"] = ["想要上台"]
        output = {
            "intent": "boundary", "business": "", "mentioned_businesses": [],
            "clarification": "", "business_requests": [],
        }
        validation = self.run_node["business_validation"](output, self.snapshot, "message", "明日天氣點？")
        self.assertFalse(validation["retry"], validation["error"])
        prepared = self.run_node["prepare"](validation["text"], self.snapshot, "message", "明日天氣點？")
        self.assertEqual(prepared["mode"], "boundary")
        self.assertFalse(prepared["ok"])
        self.assertEqual(prepared["snapshot"], self.snapshot)
        bundle = self.run_node["scope_reply"](prepared["snapshot"], prepared["text"], prepared["mode"])["bundle"]
        self.assertFalse(bundle["persist_reply"])

    def test_ambiguous_availability_can_search_without_changing_saved_conditions(self):
        existing = {"id": "R1", "text": "每月$150內", "priority": "required",
                    "attitude": "interested", "evidence": ["$150內"]}
        self.snapshot["ms_needs"].update(requirements=[existing], next_requirement_id=2)
        message = "有冇包日本漫遊嘅？"
        bundle = self.merge(message, turn_intent="availability",
                            turn_conditions=[{"text": "包含日本漫遊", "evidence": [message]}])
        current = json.loads(self.run_node["decision_state"](bundle)["needs_json"])
        self.assertEqual([item["id"] for item in current["requirements"]], ["R1", "T1"])
        self.assertEqual(bundle["snapshot"]["ms_needs"]["requirements"], [existing])
        self.assertEqual(bundle["snapshot"]["ms_needs"]["questions"], [])
        alternative = self.merge("如果睇$200內有冇？", turn_intent="availability",
                                 temporarily_excluded_ids=["R1"],
                                 turn_conditions=[{"text": "每月$200內", "evidence": ["$200內"]}])
        current = json.loads(self.run_node["decision_state"](alternative)["needs_json"])
        self.assertEqual([item["id"] for item in current["requirements"]], ["T1"])
        self.assertEqual(alternative["snapshot"]["ms_needs"]["requirements"], [existing])

    def test_separate_purchases_resume_with_their_own_conditions(self):
        existing = {"id": "R1", "text": "每月$150內", "priority": "required",
                    "attitude": "interested", "evidence": ["$150內"]}
        self.snapshot["ms_needs"].update(requirements=[existing], next_requirement_id=2)
        message = "另外幫屋企人開張卡，要50GB"
        family = self.merge(message, purchase_switch={"target": "new", "evidence": [message]},
                            changes=[{"business": "MS", "operation": "add", "requirement_id": "", "text": "50GB",
                                      "attitude": "interested", "priority": "required", "evidence": ["要50GB"]}])
        self.snapshot = family["snapshot"]
        active = self.snapshot["ms_needs"]
        self.assertEqual([item["text"] for item in active["requirements"]], ["50GB"])
        self.assertEqual(active["purchase_archive"]["S1"]["requirements"], [existing])
        self.assertTrue(self.run_node["ingress"]("返去我嗰張", "message", "", **self.snapshot)["ok"])
        resumed = self.merge("返去我嗰張", purchase_switch={"target": "S1", "evidence": ["我嗰張"]})
        self.assertEqual(resumed["snapshot"]["ms_needs"]["requirements"], [existing])

    def test_lookup_limits_catalog_and_preserves_an_open_question(self):
        self.snapshot["ms_needs"].update(
            last_reply="你每月大概用幾多GB？", interview_pending=True, last_interview_topic="data",
        )
        message = "頭先嗰款有冇漫遊？"
        bundle = self.merge(message, turn_intent="lookup", question_changes=[
            {"business": "MS", "operation": "add", "question_id": "", "text": message, "evidence": [message]}
        ])
        self.assertEqual(bundle["snapshot"]["ms_needs"]["interview_rounds"], 0)
        prepared = self.run_node["record_reply"](bundle["snapshot"], "MS", "lookup", "嗰款包日本漫遊。", "[]", True, "")
        needs = prepared["snapshot"]["ms_needs"]
        self.assertEqual(needs["questions"], [])
        self.assertEqual(needs["last_reply"], "你每月大概用幾多GB？")
        self.assertTrue(needs["interview_pending"])

    def test_search_reads_full_sellable_scope_but_lookup_reads_only_target(self):
        tags_result = self.run_node["fetch_tags"]("MS", self.snapshot)
        plans = tags_result["bundle"]["plans_tags"]
        self.snapshot["ms_needs"]["ready_to_recommend"] = True
        self.snapshot["ms_catalog_status"] = "ready"
        first = plans[0]["plan_code"]
        shortlist = json.dumps({"selected_plan_codes": [first], "reason": "優先核對"})
        for mode, expected in (
            ("recommend", [plan["plan_code"] for plan in plans]),
            ("availability", [plan["plan_code"] for plan in plans]),
            ("lookup", [first]),
        ):
            with self.subTest(mode=mode):
                result = self.run_node["rank"]({"snapshot": self.snapshot, "business": "MS", "mode": mode}, plans, shortlist)
                self.assertEqual(result["error"], "")
                self.assertEqual([item["plan_code"] for item in json.loads(result["candidates_json"])], expected)

    def test_available_query_flows_through_details_without_entering_recommendation_mode(self):
        message = "有冇包日本漫遊嘅？"
        bundle = self.merge(message, turn_intent="availability",
                            turn_conditions=[{"text": "包含日本漫遊", "evidence": [message]}])
        plans = self.run_node["fetch_tags"]("MS", self.snapshot)["bundle"]["plans_tags"]
        bundle["snapshot"]["ms_catalog_status"] = "ready"
        choice = {"action": "recommend", "selected_plan_codes": [], "follow_up": None, "reason": "查有冇"}
        validated = self.run_node["shortlist_validation"](bundle, plans, choice)
        self.assertFalse(validated["retry"], validated["error"])
        decision = self.run_node["product_decision"](bundle, plans, validated["text"])
        self.assertEqual(decision["bundle"]["mode"], "availability")
        review = self.nodes["recommend_route"]["cases"][0]
        self.assertEqual(review["logical_operator"], "or")
        self.assertIn("availability", [item["value"] for item in review["conditions"]])
        candidates = [{"plan_code": "MATCH", "markdown": "包括日本漫遊"}]
        output = {"reason": "有符合條件", "assessments": [{
            "plan_code": "MATCH", "info_answers": [],
            "requirements": [{
                "requirement_id": "T1", "status": "supported", "quote": "包括日本漫遊", "explanation": "符合",
            }],
        }]}
        needs_json = self.run_node["decision_state"](bundle)["needs_json"]
        result = self.run_node["validate_selection"](json.dumps(output), json.dumps(candidates), needs_json)
        reply = self.run_node["recommendation"](decision["bundle"], result["recommendations_json"], "", "")["bundle"]
        self.assertEqual(reply["mode"], "availability")
        self.assertEqual([plan["plan_code"] for plan in reply["selected_plans"]], ["MATCH"])
        self.assertEqual(reply["snapshot"]["ms_needs"]["requirements"], [])

    def test_unverified_required_condition_blocks_old_plan_and_removes_history_from_reply(self):
        self.snapshot["ms_needs"].update(last_recommended_codes=["OLD"], last_reply="推薦舊計劃 OLD")
        bundle = {"snapshot": self.snapshot, "business": "MS", "mode": "availability", "instruction": "",
                  "selected_plans": [], "persist_reply": True, "interview_topic": "",
                  "turn_requirements": [{"id": "T1", "text": "包含日本漫遊", "priority": "required"}]}
        needs = {"requirements": bundle["turn_requirements"], "questions": []}
        candidates = [{"plan_code": "OLD", "markdown": "本地數據 50GB"}]
        for status, quote in (("unsupported", "本地數據 50GB"), ("unknown", "")):
            with self.subTest(status=status):
                output = {"reason": "核對本輪要求", "assessments": [{
                    "plan_code": "OLD", "info_answers": [],
                    "requirements": [{"requirement_id": "T1", "status": status, "quote": quote, "explanation": "未能符合"}],
                }]}
                result = self.run_node["validate_selection"](json.dumps(output), json.dumps(candidates), json.dumps(needs))
                self.assertEqual(result["error"], "")
                self.assertEqual(json.loads(result["recommendations_json"]), [])
                reply = self.run_node["recommendation"](bundle, result["recommendations_json"], "", "")["bundle"]
                self.assertEqual(reply["mode"], "no_match")
                context = self.run_node["reply_context"](reply, "有冇包日本漫遊嘅？")
                self.assertEqual(context["selected_plans_json"], "[]")
                self.assertNotIn("OLD", context["needs_json"])
                self.assertEqual(reply["instruction"], "我哋暫時冇符合你今次要求嘅計劃。")
