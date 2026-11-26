import copy
import json
import unittest
from unittest.mock import patch

from pathlib import Path

import yaml

from dify import workflow_logic


WORKFLOW = Path(__file__).resolve().parents[1] / "dify" / "AIO Multi Business Structured.yml"


class CustomerTurnPolicyTests(unittest.TestCase):
    defaults = workflow_logic.DEFAULTS
    run_node = vars(workflow_logic)

    @classmethod
    def setUpClass(cls):
        graph = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["workflow"]["graph"]
        cls.nodes = {node["id"]: node["data"] for node in graph["nodes"]}

    def setUp(self):
        self.snapshot = copy.deepcopy(self.defaults)

    def merge(self, message, business="MS", **decisions):
        self.snapshot[business.lower() + "_needs"]["pending_messages"] = [message]
        result = {
            "changes": [], "question_changes": [], "clarification": "", "reason": "",
            "context_update": None, "turn_intent": "shop", "turn_conditions": [], "purchase_switch": None,
            "temporarily_excluded_ids": [],
            **decisions,
        }
        validated = self.run_node["extract_validation"](result, self.snapshot, business)
        self.assertFalse(validated["retry"], validated["error"])
        return self.run_node["merge"](validated["text"], self.snapshot, business)["bundle"]

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
        message = "照頭先預算，有冇包日本漫遊嘅？"
        bundle = self.merge(message, turn_intent="availability", availability_scope="current_purchase",
                            turn_conditions=[{"text": "包含日本漫遊", "evidence": ["有冇包日本漫遊嘅？"]}])
        current = json.loads(self.run_node["decision_state"](bundle)["needs_json"])
        self.assertEqual([item["id"] for item in current["requirements"]], ["R1", "T1"])
        self.assertEqual(bundle["snapshot"]["ms_needs"]["requirements"], [existing])
        self.assertEqual(bundle["snapshot"]["ms_needs"]["questions"], [])
        alternative = self.merge("其他照舊，如果睇$200內有冇？", turn_intent="availability",
                                 availability_scope="current_purchase",
                                 temporarily_excluded_ids=["R1"],
                                 turn_conditions=[{"text": "每月$200內", "evidence": ["$200內"]}])
        current = json.loads(self.run_node["decision_state"](alternative)["needs_json"])
        self.assertEqual([item["id"] for item in current["requirements"]], ["T1"])
        self.assertEqual(alternative["snapshot"]["ms_needs"]["requirements"], [existing])

    def test_xe_catalog_query_does_not_filter_by_contract_when_customer_has_no_preference(self):
        message = "有冇$80左右，不考虑合约期disney+推荐"
        bundle = self.merge(
            message, business="XE", turn_intent="availability",
            catalog_recommendation="disney+推荐",
            turn_conditions=[
                {"text": "月费约$80", "evidence": ["$80左右"]},
                {"text": "不考虑合约期", "evidence": ["不考虑合约期"]},
                {"text": "Disney+计划", "evidence": ["disney+"]},
            ],
        )
        needs = json.loads(self.run_node["decision_state"](bundle)["needs_json"])
        self.assertEqual([(item["id"], item["text"]) for item in needs["requirements"]], [
            ("T1", "月费约$80"), ("T2", "Disney+计划"),
        ])
        self.assertEqual(bundle["catalog_recommendation"], "disney+推荐")

        message = "有冇唔想綁合約嘅Disney+計劃"
        bundle = self.merge(
            message, business="XE", turn_intent="availability",
            turn_conditions=[{"text": "要求無合約期限制", "evidence": ["唔想綁合約"]}],
        )
        self.assertEqual([item["text"] for item in bundle["turn_requirements"]], ["要求無合約期限制"])

    def test_home_phone_availability_does_not_inherit_previous_broadband_filters(self):
        conditions = [
            {"id": "R1", "text": "月費$130內", "priority": "required", "attitude": "interested", "evidence": ["130內"]},
            {"id": "R2", "text": "1000M家居寬頻", "priority": "required", "attitude": "interested", "evidence": ["1000M"]},
        ]
        self.snapshot["bn_needs"].update(requirements=conditions, next_requirement_id=3)
        message = "我想要固话套餐"
        bundle = self.merge(message, business="BN", turn_intent="availability",
                            turn_conditions=[{"text": "包含家居電話", "evidence": [message]}])
        needs = json.loads(self.run_node["decision_state"](bundle)["needs_json"])
        self.assertEqual([item["id"] for item in needs["requirements"]], ["T1"])
        self.assertEqual(bundle["snapshot"]["bn_needs"]["requirements"], conditions)
        codes = ["AU14924HTOOTT_C3F24NOR30IDD", "AD19924CM_C3F24NOR30IDD"]
        snapshot = bundle["snapshot"]
        snapshot.update(bn_scope_codes=codes, bn_catalog_status="ready")
        snapshot["bn_needs"]["ready_to_recommend"] = True
        bundle["mode"] = "availability"
        plans = self.run_node["fetch_tags"]("BN", snapshot)["plans_tags"]
        ranked = self.run_node["rank"](bundle, plans, json.dumps({"selected_plan_codes": codes, "reason": "查家居電話"}))
        self.assertEqual(ranked["error"], "")
        self.assertEqual([item["id"] for item in json.loads(ranked["request_json"])["requirements"]], ["T1"])
        checked = self.run_node["validate_selection"](
            json.dumps({"reason": "有連家居電話的計劃", "assessments": [{
                "plan_code": code, "info_answers": [],
                "requirements": [{"requirement_id": "T1", "status": "supported",
                                  "quote": "- 服务内容: 家居電話, IDD 0030", "explanation": "包括家居電話"}],
            } for code in codes]}), ranked["candidates_json"], json.dumps(needs),
        )
        reply = self.run_node["recommendation"](bundle, checked["recommendations_json"], checked["error"], "")["bundle"]
        self.assertEqual(reply["mode"], "availability")
        self.assertEqual([plan["plan_monthly_fee"] for plan in reply["selected_plans"]], ["149", "199"])

        scoped = self.merge("其他條件照舊，有冇包家居電話？", business="BN", turn_intent="availability",
                            availability_scope="current_purchase",
                            turn_conditions=[{"text": "包含家居電話", "evidence": ["有冇包家居電話？"]}])
        scoped_needs = json.loads(self.run_node["decision_state"](scoped)["needs_json"])
        self.assertEqual([item["id"] for item in scoped_needs["requirements"]], ["R1", "R2", "T1"])

    def test_disney_availability_preserves_the_plan_shown_for_the_promotion_followup(self):
        code = "AX12924DPIQOEX"
        self.snapshot["bn_scope_codes"] = [code]
        self.snapshot["bn_needs"]["last_recommended_codes"] = ["OLD"]
        text = f"有，1000M 可揀 12 個月 Disney+ 標準計劃，參考月費 $129（{code}）。"
        displayed = json.dumps([{"plan_code": code}, {"plan_code": "AB99999"}])
        result = self.run_node["record_reply"](self.snapshot, "BN", "availability", text, displayed, True, "")
        self.assertEqual(result["snapshot"]["bn_needs"]["last_recommended_codes"], [code])
        self.snapshot = result["snapshot"]
        message = "我想加多个D+一期等级，有没有优惠？"
        bundle = self.merge(message, business="BN", turn_intent="lookup", question_changes=[{
            "business": "BN", "operation": "add", "question_id": "",
            "text": "上一轮宽频连 Disney+ 组合有无优惠", "evidence": [message],
        }])
        snapshot = bundle["snapshot"]
        catalog = self.run_node["fetch_tags"]("BN", snapshot)["plans_tags"]
        snapshot.update(bn_plans_tags=catalog, bn_catalog_status="ready")
        state = self.run_node["decision_state"](bundle)
        self.assertEqual(json.loads(state["comparison_context_json"])["previous_recommendations"][0]["plan_code"], code)
        self.assertEqual(json.loads(state["needs_json"])["requirements"], [])
        decision = self.run_node["product_decision"](bundle, catalog, json.dumps({
            "action": "recommend", "selected_plan_codes": [code], "follow_up": None, "reason": "查同一計劃",
        }))
        bundle = decision["bundle"]
        ranked = self.run_node["rank"](bundle, catalog, decision["shortlist_json"])
        self.assertEqual(ranked["error"], "")
        checked = self.run_node["validate_selection"](json.dumps({
            "reason": "原文列明預繳及回贈", "assessments": [{
                "plan_code": code, "requirements": [], "info_answers": [{
                    "question_id": "Q1", "status": "answered",
                    "quote": "- 預繳HK$200，第1至第4個月每月回贈$50",
                    "answer": "預繳$200，首4個月每月回贈$50",
                }],
            }],
        }), ranked["candidates_json"], state["needs_json"])
        result = self.run_node["recommendation"](bundle, checked["recommendations_json"], checked["error"], "")["bundle"]
        self.assertEqual(result["mode"], "lookup")
        self.assertEqual(result["selected_plans"][0]["plan_monthly_fee"], "129")

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
        self.assertEqual(json.loads(active["purchase_archive"])["S1"]["requirements"], [existing])
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

    def mobile_catalog(self, intent):
        plans = self.run_node["fetch_tags"]("MS", self.snapshot)["plans_tags"]
        self.snapshot["ms_catalog_status"] = "ready"
        self.snapshot["ms_needs"]["user_context"] = "想搵手機上台計劃"
        bundle = {"snapshot": self.snapshot, "business": "MS", "instruction": "", "turn_intent": intent}
        return bundle, plans, [plan["plan_code"] for plan in plans]

    def shortlist(self, bundle, plans, codes):
        choice = {"action": "recommend", "selected_plan_codes": codes, "follow_up": None, "reason": "按標籤核對"}
        validated = self.run_node["shortlist_validation"](bundle, plans, choice)
        self.assertFalse(validated["retry"], validated["error"])
        return self.run_node["product_decision"](bundle, plans, validated["text"])

    def test_only_shortlisted_documents_reach_selection_in_shortlist_order(self):
        for intent, mode, count in (("shop", "recommend", 5), ("availability", "availability", 5), ("lookup", "lookup", 1)):
            with self.subTest(intent=intent):
                bundle, plans, codes = self.mobile_catalog(intent)
                self.assertGreater(len(codes), 5)
                selected = list(reversed(codes))[:count]
                decision = self.shortlist(bundle, plans, selected)
                self.assertEqual(decision["bundle"]["mode"], mode)
                rank = self.run_node["rank"]
                # 未入选套餐在标签目录中可售，但详情服务只有本轮请求的原文。
                details = rank.__globals__["DETAILS"]["MS"]
                with patch.dict(details, {code: details[code] for code in selected}, clear=True):
                    result = rank(decision["bundle"], plans, decision["shortlist_json"])
                self.assertEqual(result["error"], "")
                documents = json.loads(result["candidates_json"])
                self.assertEqual([plan["plan_code"] for plan in documents], selected)
                for plan in documents:
                    source = WORKFLOW.parents[1] / "data" / "new_data" / (plan["plan_code"] + "-cn")
                    self.assertEqual(plan["markdown"], source.read_text(encoding="utf-8"))
                sales = json.loads(result["sales_candidates_json"])
                self.assertEqual([plan["plan_code"] for plan in sales], selected)

    def test_empty_shortlist_bypasses_details_and_selection(self):
        detail_modes = {item["value"] for item in self.nodes["recommend_route"]["cases"][0]["conditions"]}
        for intent, mode in (("shop", "no_match"), ("availability", "no_match"), ("lookup", "lookup_unavailable")):
            with self.subTest(intent=intent):
                bundle, plans, _ = self.mobile_catalog(intent)
                decision = self.shortlist(bundle, plans, [])
                self.assertEqual(decision["bundle"]["mode"], mode)
                self.assertNotIn(mode, detail_modes)
                self.assertEqual(decision["bundle"].get("selected_plans", []), [])

    def test_shortlist_rejects_excess_duplicate_and_out_of_scope_codes(self):
        bundle, plans, codes = self.mobile_catalog("availability")
        for invalid in (codes[:6], [codes[0], codes[0]], [codes[0], "UNKNOWN"]):
            with self.subTest(invalid=invalid):
                choice = {"action": "recommend", "selected_plan_codes": invalid, "follow_up": None, "reason": "核對"}
                self.assertTrue(self.run_node["shortlist_validation"](bundle, plans, choice)["retry"])
                decision = self.run_node["product_decision"](bundle, plans, json.dumps(choice))
                self.assertEqual(decision["bundle"]["mode"], "error")
                self.assertEqual(json.loads(decision["shortlist_json"])["selected_plan_codes"], [])

    def test_selection_accepts_zero_to_two_and_rejects_codes_outside_shortlist(self):
        bundle, plans, codes = self.mobile_catalog("shop")
        decision = self.shortlist(bundle, plans, codes[:5])
        ranked = self.run_node["rank"](decision["bundle"], plans, decision["shortlist_json"])
        self.assertEqual(ranked["error"], "")
        candidates = ranked["sales_candidates_json"]
        needs = self.run_node["decision_state"](decision["bundle"])["needs_json"]
        for selected, valid in (([], True), (codes[1:2], True), ([codes[4], codes[1]], True),
                                (codes[:3], False), ([codes[0], codes[0]], False), ([codes[0], codes[5]], False)):
            with self.subTest(selected=selected):
                output = {"reason": "按原文核對", "assessments": [
                    {"plan_code": code, "requirements": [], "info_answers": []} for code in selected
                ]}
                validated = self.run_node["select_validation"](output, candidates, needs)
                self.assertEqual(validated["retry"], not valid, validated["error"])
                result = self.run_node["validate_selection"](validated["text"], candidates, needs)
                self.assertEqual(bool(result["error"]), not valid, result["error"])
                self.assertEqual([p["plan_code"] for p in json.loads(result["recommendations_json"])],
                                 selected if valid else [])

    def test_available_query_flows_through_details_without_entering_recommendation_mode(self):
        message = "有冇包每月3GB中國內地及澳門數據嘅？"
        bundle = self.merge(message, turn_intent="availability",
                            turn_conditions=[{"text": "每月3GB中國內地及澳門數據", "evidence": [message]}])
        plans = self.run_node["fetch_tags"]("MS", self.snapshot)["plans_tags"]
        bundle["snapshot"]["ms_catalog_status"] = "ready"
        code = plans[0]["plan_code"]
        choice = {"action": "recommend", "selected_plan_codes": [code], "follow_up": None, "reason": "查有冇"}
        validated = self.run_node["shortlist_validation"](bundle, plans, choice)
        self.assertFalse(validated["retry"], validated["error"])
        decision = self.run_node["product_decision"](bundle, plans, validated["text"])
        self.assertEqual(decision["bundle"]["mode"], "availability")
        review = self.nodes["recommend_route"]["cases"][0]
        self.assertEqual(review["logical_operator"], "or")
        self.assertIn("availability", [item["value"] for item in review["conditions"]])
        ranked = self.run_node["rank"](decision["bundle"], plans, decision["shortlist_json"])
        self.assertEqual(ranked["error"], "")
        output = {"reason": "有符合條件", "assessments": [{
            "plan_code": code, "info_answers": [],
            "requirements": [{
                "requirement_id": "T1", "status": "supported", "quote": "每月3GB中國內地及澳門數據", "explanation": "符合",
            }],
        }]}
        needs_json = self.run_node["decision_state"](bundle)["needs_json"]
        result = self.run_node["validate_selection"](json.dumps(output), ranked["sales_candidates_json"], needs_json)
        self.assertEqual(result["error"], "")
        reply = self.run_node["recommendation"](decision["bundle"], result["recommendations_json"], "", "")["bundle"]
        self.assertEqual(reply["mode"], "availability")
        self.assertEqual([plan["plan_code"] for plan in reply["selected_plans"]], [code])
        self.assertEqual(reply["snapshot"]["ms_needs"]["requirements"], [])

    def test_explicit_catalog_recommendation_gets_links_without_changing_saved_conditions(self):
        for message, evidence, expected_action in (
            ("推介包日本漫遊嘅計劃", "推介包日本漫遊嘅計劃", "SHOW_PRODUCT_LINK"),
            ("有冇包日本漫遊嘅？", "", "NONE"),
            ("有冇包日本漫遊嘅？", "推介包日本漫遊嘅計劃", "NONE"),
        ):
            with self.subTest(message=message, evidence=evidence):
                bundle = self.merge(
                    message, turn_intent="availability", catalog_recommendation=evidence,
                    turn_conditions=[{"text": "包含日本漫遊", "evidence": [message]}],
                )
                plans = self.run_node["fetch_tags"]("MS", self.snapshot)["plans_tags"]
                bundle["snapshot"]["ms_catalog_status"] = "ready"
                code = plans[0]["plan_code"]
                decision = self.run_node["product_decision"](bundle, plans, json.dumps({
                    "action": "recommend", "selected_plan_codes": [code], "follow_up": None, "reason": "核對目錄",
                }))
                self.assertEqual(decision["bundle"]["mode"], "availability")
                checked = self.run_node["validate_selection"](
                    json.dumps({"reason": "有符合條件", "assessments": [{
                        "plan_code": code, "info_answers": [], "requirements": [{
                            "requirement_id": "T1", "status": "supported",
                            "quote": "包括日本漫遊", "explanation": "符合",
                        }],
                    }]}),
                    json.dumps([{"plan_code": code, "markdown": "包括日本漫遊"}]),
                    self.run_node["decision_state"](bundle)["needs_json"],
                )
                self.assertEqual(checked["error"], "")
                reply = self.run_node["recommendation"](
                    decision["bundle"], checked["recommendations_json"], "", "",
                )["bundle"]
                self.assertEqual(reply["snapshot"]["ms_needs"]["requirements"], [])
                context = self.run_node["reply_context"](reply, message)
                self.assertEqual(json.loads(context["selected_plans_json"])[0]["plan_code"], code)
                action = self.product_link(context)
                self.assertEqual(action["frontend_action"], expected_action)
                self.assertEqual("按推薦版式" in context["instruction"], expected_action == "SHOW_PRODUCT_LINK")
                self.assertEqual(action["products"], (
                    [{"plan_code": code, "link_label": "查看計劃"}] if expected_action == "SHOW_PRODUCT_LINK" else []
                ))
                saved = self.record_reply(context, "呢款包日本漫遊。")
                self.assertEqual(saved["ms_needs"]["last_recommended_codes"],
                                 [code] if expected_action == "SHOW_PRODUCT_LINK" else [])

    def record_reply(self, context, text):
        outputs = {"reply_context": context, "reply": {"text": text}}
        inputs = {}
        for item in self.nodes["record_reply"]["variables"]:
            node, field = item["value_selector"]
            inputs[item["variable"]] = outputs[node][field]
        return self.run_node["record_reply"](**inputs)["snapshot"]

    def test_catalog_recommendation_caches_both_codes_in_link_order_for_follow_up(self):
        plans = self.run_node["fetch_tags"]("MS", self.snapshot)["plans_tags"]
        self.snapshot["ms_plans_tags"] = plans
        self.snapshot["ms_needs"]["interview_pending"] = True
        codes = [plans[1]["plan_code"], plans[0]["plan_code"]]
        message = "推介包日本漫遊嘅計劃"
        bundle = {
            "snapshot": self.snapshot, "business": "MS", "mode": "availability", "instruction": "",
            "persist_reply": True, "interview_topic": "", "catalog_recommendation": message,
            "selected_plans": [{"plan_code": code, "markdown": "包括日本漫遊"} for code in codes],
        }
        context = self.run_node["reply_context"](bundle, message)
        saved = self.record_reply(context, f"兩款都包日本漫遊，{codes[0]} 數據較多。")
        self.assertEqual(saved["ms_needs"]["last_recommended_codes"], codes)
        self.assertTrue(saved["ms_needs"]["interview_pending"])

        next_turn = self.run_node["decision_state"]({**bundle, "snapshot": saved, "mode": "lookup"})
        comparison = json.loads(next_turn["comparison_context_json"])
        self.assertEqual([plan["plan_code"] for plan in comparison["previous_recommendations"]], codes)

    def test_bn_address_callback_keeps_validated_catalog_recommendation(self):
        message = "有無咩寬頻Plan推介？"
        codes = ["AX9836ROB", "AU14924HTOOTT_C3F24NOR30IDD"]
        self.snapshot["bn_scope_codes"] = codes
        bundle = self.merge(
            message, business="BN", turn_intent="availability", catalog_recommendation=message,
            turn_conditions=[{"text": "家居宽频计划", "evidence": [message]}],
        )
        bundle.update(mode="availability", selected_plans=[
            {"plan_code": code, "markdown": "提供家居寬頻"} for code in codes
        ])
        context = self.run_node["reply_context"](bundle, "")
        self.assertIn("按推薦版式依照以下計劃順序", context["instruction"])
        self.assertIn(f"（{codes[0]}）", context["instruction"])
        action = self.product_link(context)
        self.assertEqual(action["frontend_action"], "SHOW_PRODUCT_LINK")
        self.assertEqual([plan["plan_code"] for plan in action["products"]], codes)

    def test_ms_catalog_preference_does_not_block_a_verified_candidate(self):
        message = "有冇流动通讯plan推荐？最好流量高"
        bundle = self.merge(
            message, turn_intent="availability", catalog_recommendation=message,
            turn_conditions=[{"text": "流动通讯计划流量高", "evidence": ["最好流量高"]}],
        )
        needs = self.run_node["decision_state"](bundle)["needs_json"]
        self.assertEqual(json.loads(needs)["requirements"][0]["priority"], "preferred")
        code = "MC5G100G3BAC"
        checked = self.run_node["validate_selection"](
            json.dumps({"reason": "已核对", "assessments": [{
                "plan_code": code, "info_answers": [], "requirements": [{
                    "requirement_id": "T1", "status": "unknown", "quote": "",
                    "explanation": "原文明示100GB，用户未给流量门槛",
                }],
            }]}),
            json.dumps([{"plan_code": code, "markdown": "本地流動數據:100GB"}]), needs,
        )
        self.assertEqual(checked["error"], "")
        self.assertEqual([item["plan_code"] for item in json.loads(checked["recommendations_json"])], [code])
        hard = self.merge(
            "至少100GB", turn_intent="availability",
            turn_conditions=[{"text": "至少100GB", "priority": "required", "evidence": ["至少100GB"]}],
        )
        self.assertEqual(hard["turn_requirements"][0]["priority"], "required")

        mixed = self.merge(
            message, turn_intent="availability",
            turn_conditions=[{"text": "流动通讯计划，最好流量高",
                              "evidence": ["流动通讯plan", "最好流量高"]}],
        )
        self.assertEqual(mixed["turn_requirements"][0]["priority"], "preferred")
        self.assertEqual(mixed["turn_requirements"][0]["evidence"], ["最好流量高"])

    def test_bn_temporary_selection_delivers_ordered_links_without_recommendation_quote(self):
        message = "需要1G寬頻，月費低過$198，你哋有冇得平"
        codes = ["AX9836ROB", "AX10924DB"]
        self.snapshot.update(current_business="BN", bn_scope_codes=codes)
        bundle = self.merge(
            message, business="BN", turn_intent="availability", delivery_goal="selection",
            turn_conditions=[
                {"text": "需要1G宽频", "evidence": ["需要1G寬頻"]},
                {"text": "月费低于$198", "evidence": ["月費低過$198"]},
            ],
        )
        self.assertEqual(bundle["snapshot"]["bn_needs"]["requirements"], [])
        bundle.update(mode="availability", selected_plans=[
            {"plan_code": code, "markdown": f"方案名称: {name}"}
            for code, name in zip(codes, ["1000M連路由器", "1000M家居寬頻"])
        ])
        context = self.run_node["reply_context"](bundle, "")
        self.assertEqual(self.product_link(context)["frontend_action"], "SHOW_PRODUCT_LINK")
        self.assertEqual([item["plan_code"] for item in context["link_products"]], codes)
        self.assertEqual(context["presentation"]["plan_codes"], codes)
        headings = [line for line in context["instruction"].splitlines() if line.startswith("#####")]
        self.assertEqual(headings, [
            f"##### 第 1 款｜1000M連路由器（{codes[0]}）",
            f"##### 第 2 款｜1000M家居寬頻（{codes[1]}）",
        ])
        saved = self.record_reply(context, "兩款主要分別係合約期。")
        self.assertEqual(saved["bn_needs"]["last_recommended_codes"], codes)

        self.snapshot = copy.deepcopy(saved)
        question = "第二款係咪自動續約？"
        facts = self.merge(question, business="BN", turn_intent="lookup", delivery_goal="facts",
                           question_changes=[{"business": "BN", "operation": "add", "question_id": "",
                                              "text": question, "evidence": [question]}])
        facts.update(mode="lookup", selected_plans=[bundle["selected_plans"][1]])
        context = self.run_node["reply_context"](facts, question)
        self.assertEqual(self.product_link(context)["frontend_action"], "NONE")
        self.snapshot = self.record_reply(context, "暫時未核實自動續約條款。")
        self.assertEqual(self.snapshot["bn_needs"]["last_recommended_codes"], codes)
        link = "畀第二款嘅辦理連結"
        lookup = self.merge(link, business="BN", turn_intent="lookup", delivery_goal="link",
                            link_request=link, question_changes=[
                                {"business": "BN", "operation": "add", "question_id": "",
                                 "text": link, "evidence": [link]},
                            ])
        tags = self.run_node["fetch_tags"]("BN", lookup["snapshot"])["plans_tags"]
        choice = {"action": "recommend", "selected_plan_codes": [codes[0]], "follow_up": None, "reason": "核對"}
        wrong = self.run_node["product_decision"](lookup, tags, json.dumps(choice))
        self.assertEqual(wrong["bundle"]["mode"], "error")
        choice["selected_plan_codes"] = [codes[1]]
        correct = self.run_node["product_decision"](lookup, tags, json.dumps(choice))
        self.assertEqual(correct["bundle"]["mode"], "lookup")
        correct["bundle"]["selected_plans"] = [bundle["selected_plans"][1]]
        self.assertEqual(self.product_link(self.run_node["reply_context"](correct["bundle"], link))["products"][0]["plan_code"],
                         codes[1])

        changed = self.run_node["ingress"]("已更改地址", "address_change", "AX10924DB", **saved)
        self.assertTrue(changed["ok"], changed["error"])
        self.assertEqual(changed["snapshot"]["bn_needs"]["last_recommended_codes"], [])
        resumed = self.run_node["merge"]("", changed["snapshot"], "BN", "address_change")["bundle"]
        self.assertEqual(resumed["delivery_goal"], "selection")
        self.assertEqual(resumed["turn_requirements"], bundle["turn_requirements"])
        self.assertEqual(resumed["snapshot"]["bn_scope_codes"], ["AX10924DB"])
        self.assertEqual(resumed["selected_plans"], [])
        stale = {**resumed, "mode": "availability", "selected_plans": [bundle["selected_plans"][0]]}
        context = self.run_node["reply_context"](stale, "")
        self.assertEqual(context["mode"], "no_match")
        self.assertNotIn("按推薦版式", context["instruction"])
        self.assertEqual(self.product_link(context)["frontend_action"], "NONE")

    def test_no_match_still_passes_explicit_questions_and_current_evidence(self):
        message = "我想加多个D+一起登记，有没优惠？"
        bundle = self.merge(message, question_changes=[{
            "business": "MS", "operation": "add", "question_id": "",
            "text": "一起登记有没优惠？", "evidence": ["有没优惠？"],
        }])
        bundle["mode"] = "recommend"
        candidate = {"plan_code": "MT5G100GCNKD", "markdown": "回贈至同時登記之指定寬頻服務計劃"}
        result = self.run_node["recommendation"](bundle, "[]", "", "", json.dumps([candidate]))["bundle"]
        context = self.run_node["reply_context"](result, message)
        needs = json.loads(context["needs_json"])
        self.assertEqual(context["mode"], "no_match")
        self.assertEqual([question["text"] for question in needs["questions"]], ["一起登记有没优惠？"])
        self.assertEqual(needs["consultation_plans"], [candidate])
        self.assertEqual(json.loads(context["selected_plans_json"]), [])
        self.assertEqual(context["link_products"], [])

        failed = self.run_node["recommendation"](bundle, "[]", "", "讀取失敗", json.dumps([candidate]))["bundle"]
        context = self.run_node["reply_context"](failed, message)
        self.assertEqual(context["mode"], "error")
        self.assertNotIn("consultation_plans", json.loads(context["needs_json"]))

    def test_alternatives_inherit_purchase_conditions_and_skip_last_display_after_error(self):
        codes = ["AX9836ROB", "AX10924DB", "AX12924DPIQOEX"]
        self.snapshot.update(current_business="BN", bn_scope_codes=codes)
        offer = self.merge(
            "需要1G寬頻，月費低過$198", business="BN", turn_intent="availability", delivery_goal="selection",
            turn_conditions=[
                {"text": "需要1G宽频", "evidence": ["需要1G寬頻"]},
                {"text": "月费低于$198", "evidence": ["月費低過$198"]},
            ],
        )
        offer.update(mode="availability", selected_plans=[
            {"plan_code": code, "markdown": "方案名称: 1000M家居寬頻"} for code in codes[:2]
        ])
        context = self.run_node["reply_context"](offer, "")
        self.snapshot = self.record_reply(context, "兩款都係1000M。")
        first_snapshot = copy.deepcopy(self.snapshot)
        added = self.merge("想加埋Disney+", business="BN", selection_context="continue", changes=[
            {"business": "BN", "operation": "add", "requirement_id": "", "text": "包含Disney+",
             "attitude": "interested", "priority": "required", "evidence": ["想加埋Disney+"]},
        ])
        requirements = json.loads(self.run_node["decision_state"](added)["needs_json"])["requirements"]
        self.assertEqual([item["text"] for item in requirements], ["包含Disney+", "需要1G宽频", "月费低于$198"])
        self.snapshot = first_snapshot
        failed = self.run_node["reply_context"]({**offer, "snapshot": self.snapshot, "mode": "error"}, "")
        self.assertEqual(failed["link_products"], [])
        self.snapshot = self.record_reply(failed, "今次未能完成核對。")
        other = self.merge("还有其他plan吗", business="BN", turn_intent="availability",
                           delivery_goal="facts", turn_conditions=[])
        self.assertEqual(other["delivery_goal"], "selection")
        self.assertEqual(other["excluded_plan_codes"], codes[:2])
        self.assertEqual([item["text"] for item in other["turn_requirements"]],
                         ["需要1G宽频", "月费低于$198"])
        tags = self.run_node["fetch_tags"]("BN", other["snapshot"])["plans_tags"]
        choice = {"action": "recommend", "selected_plan_codes": [codes[0]], "follow_up": None, "reason": "查找"}
        self.assertEqual(self.run_node["product_decision"](other, tags, json.dumps(choice))["bundle"]["mode"], "error")
        choice["selected_plan_codes"] = [codes[2]]
        decision = self.run_node["product_decision"](other, tags, json.dumps(choice))["bundle"]
        decision["selected_plans"] = [{"plan_code": codes[2], "markdown": "方案名称: 1000M寬頻"}]
        current = self.run_node["reply_context"](decision, "")
        self.assertEqual(self.product_link(current)["products"][0]["plan_code"], codes[2])
        self.snapshot = self.record_reply(current, "呢款月費較平。")
        more = self.merge("有冇其他plan", business="BN", turn_intent="availability", delivery_goal="facts")
        self.assertEqual(more["excluded_plan_codes"], codes)
        new = self.merge("想查月費高過$300嘅寬頻", business="BN", turn_intent="availability",
                         delivery_goal="facts", selection_context="new",
                         turn_conditions=[{"text": "月费高于$300", "evidence": ["月費高過$300"]}])
        self.assertEqual([item["text"] for item in new["turn_requirements"]], ["月费高于$300"])
        self.assertNotIn("excluded_plan_codes", new)
        changed = self.merge("預算改為$300內", business="BN", turn_intent="availability",
                             delivery_goal="selection", selection_context="continue",
                             replaced_temporary_ids=["T2"],
                             turn_conditions=[{"text": "月费不超过$300", "evidence": ["預算改為$300內"]}])
        self.assertEqual([item["text"] for item in changed["turn_requirements"]], ["需要1G宽频", "月费不超过$300"])

    def test_required_monthly_limit_blocks_out_of_budget_plan_even_with_supported_assessment(self):
        code = "AE99824TP54OTT"
        candidates = [{"plan_code": code, "plan_monthly_fee": "998", "markdown": "套餐参考月费: 998"}]
        needs = {"requirements": [{"id": "T1", "text": "月费低于$198", "priority": "required"}]}
        selection = {"reason": "回放", "assessments": [{
            "plan_code": code, "requirements": [{
                "requirement_id": "T1", "status": "supported",
                "quote": "套餐参考月费: 998", "explanation": "误判为符合预算",
            }],
        }]}
        checked = self.run_node["validate_selection"](json.dumps(selection), json.dumps(candidates), json.dumps(needs))
        self.assertEqual(checked["error"], "")
        self.assertEqual(json.loads(checked["recommendations_json"]), [])

    def test_withdrawing_optional_service_removes_requirement(self):
        self.snapshot["ms_needs"].update(requirements=[
            {"id": "R1", "text": "加配Disney+服务", "attitude": "interested", "priority": "required", "evidence": []},
            {"id": "R2", "text": "月费低于$381", "attitude": "interested", "priority": "required", "evidence": []},
        ], next_requirement_id=3)
        message = "唔加配Disney+服務"
        bundle = self.merge(message, changes=[
            {"business": "MS", "operation": "update", "requirement_id": "R1",
             "text": "不加配Disney+服务", "attitude": "uninterested", "priority": "required", "evidence": [message]},
        ])
        self.assertEqual([item["id"] for item in bundle["snapshot"]["ms_needs"]["requirements"]], ["R2"])
        message = "不要包含Disney+服务"
        excluded = self.merge(message, changes=[
            {"business": "MS", "operation": "update", "requirement_id": "R1",
             "text": message, "attitude": "uninterested", "priority": "required", "evidence": [message]},
        ])
        self.assertEqual(excluded["snapshot"]["ms_needs"]["requirements"][0]["attitude"], "uninterested")
        self.snapshot["ms_needs"]["requirements"][0].update(
            text="不加配Disney+服务", attitude="uninterested", evidence=["唔加配Disney+服務"]
        )
        legacy = self.merge("不要Disney+服務了", changes=[])
        self.assertEqual([item["id"] for item in legacy["snapshot"]["ms_needs"]["requirements"]], ["R2"])

    def test_shortlist_retries_compatibly_with_literal_boolean_strings(self):
        bundle = self.merge("我想揀上台計劃")
        plans = self.run_node["fetch_tags"]("MS", bundle["snapshot"])["plans_tags"]
        result = {"action": "clarify", "selected_plan_codes": [], "reason": "對象未能確定",
                  "follow_up": {"topic": "計劃指代", "information_needed": "你指邊一款？",
                                "selection_impact": "按指定計劃核對", "blocking": "true"}}
        validated = self.run_node["shortlist_validation"](bundle, plans, result)
        self.assertFalse(validated["retry"], validated["error"])
        self.assertIs(json.loads(validated["text"])["follow_up"]["blocking"], True)

    def test_reply_context_distinguishes_router_terms_and_surfaces_speed_conflict(self):
        for business, codes in (
            ("BN", ["AX9836ROB", "AW37827F3PROV", "AU14924HTOOTT_C3F24NOR30IDD"]),
            ("MS", ["MC5G100G3BAC"]),
        ):
            with self.subTest(business=business):
                self.snapshot["bn_scope_codes"] = codes if business == "BN" else []
                self.snapshot[business.lower() + "_catalog_status"] = "ready"
                self.snapshot[business.lower() + "_needs"]["ready_to_recommend"] = True
                plans = self.run_node["fetch_tags"](business, self.snapshot)["plans_tags"]
                bundle = {
                    "snapshot": self.snapshot, "business": business, "mode": "recommend", "instruction": "",
                    "persist_reply": True, "interview_topic": "",
                }
                ranked = self.run_node["rank"](bundle, plans, json.dumps({
                    "selected_plan_codes": codes, "reason": "重播",
                }))
                self.assertEqual(ranked["error"], "")
                candidates = json.loads(ranked["sales_candidates_json"])
                shown = []
                for plan in candidates:
                    bundle["selected_plans"] = [plan]
                    context = self.run_node["reply_context"](bundle, "有咩計劃推介")
                    shown.extend(json.loads(context["selected_plans_json"]))
                if business == "BN":
                    self.assertEqual([plan["router_offer"] for plan in shown],
                                     ["included", "required_add_on", "unconfirmed"])
                    marketing = candidates[-1]
                    quote = next(line for line in marketing["markdown"].splitlines() if "多元化的路由器方案" in line)
                    outcome = self.run_node["validate_selection"](
                        json.dumps({"reason": "核對路由器", "assessments": [{
                            "plan_code": marketing["plan_code"], "requirements": [{
                                "requirement_id": "T1", "status": "supported", "quote": quote,
                                "explanation": "套餐包括路由器",
                            }],
                        }]}),
                        json.dumps([marketing]),
                        json.dumps({"requirements": [{"id": "T1", "text": "包含路由器", "priority": "required"}]}),
                    )
                    self.assertEqual(outcome["error"], "")
                    self.assertEqual(json.loads(outcome["recommendations_json"]), [])
                else:
                    self.assertEqual(shown[0]["after_data_limit"]["speeds_mbps"], ["1", "5"])
                    self.assertTrue(shown[0]["after_data_limit"]["conflict"])
                    terms = shown[0]["booking_terms"]
                    self.assertTrue(any("自動續約" in term for term in terms["renewal"]))
                    self.assertTrue(terms["admin_fee_amounts_differ"])
                    self.assertTrue(any("$28" in term for term in terms["admin_fee"]))
                    self.assertTrue(any("$18" in term for term in terms["admin_fee"]))

    def product_link(self, context):
        variables = self.nodes["product_link_action"]["variables"]
        self.assertEqual([(item["variable"], item["value_selector"]) for item in variables], [
            ("products", ["reply_context", "link_products"]),
            ("service_type", ["reply_context", "business"]),
        ])
        return self.run_node["product_link_action"](context["link_products"], context["business"])

    def test_recommendation_keeps_both_links_when_model_omits_a_label(self):
        candidates = [
            {"plan_code": "PLAN_A", "markdown": "上網速度 1000M"},
            {"plan_code": "PLAN_B", "markdown": "上網速度 2500M"},
        ]
        selection = {"reason": "按需要排序", "assessments": [
            {"plan_code": "PLAN_A", "requirements": [], "link_label": "查看 1000M 方案"},
            {"plan_code": "PLAN_B", "requirements": []},
        ]}
        checked = self.run_node["validate_selection"](
            json.dumps(selection), json.dumps(candidates), '{"requirements": []}',
        )
        self.assertEqual(checked["error"], "")
        self.snapshot["bn_scope_codes"] = ["PLAN_A", "PLAN_B"]
        bundle = {"snapshot": self.snapshot, "business": "BN", "mode": "recommend",
                  "instruction": "", "persist_reply": True, "interview_topic": ""}
        reply = self.run_node["recommendation"](bundle, checked["recommendations_json"], "", "")["bundle"]
        context = self.run_node["reply_context"](reply, "推介兩款寬頻")
        products = [
            {"plan_code": "PLAN_A", "link_label": "查看 1000M 方案"},
            {"plan_code": "PLAN_B", "link_label": "查看第 2 款"},
        ]
        self.assertEqual(context["link_products"], products)
        self.assertEqual(self.product_link(context), {
            "frontend_action": "SHOW_PRODUCT_LINK", "service_type": "BN", "products": products,
        })
        selection["assessments"][1]["link_label"] = "查看 1000M 方案"
        checked = self.run_node["validate_selection"](
            json.dumps(selection), json.dumps(candidates), '{"requirements": []}',
        )
        self.assertEqual(json.loads(checked["recommendations_json"])[1]["link_label"], "")

    def test_requested_link_requires_current_evidence_and_unique_verified_plan(self):
        message = "俾條申請連結我"
        bundle = self.merge(message, turn_intent="lookup", link_request=message, question_changes=[
            {"business": "MS", "operation": "add", "question_id": "", "text": message, "evidence": [message]},
        ])
        plans = self.run_node["fetch_tags"]("MS", self.snapshot)["plans_tags"]
        bundle["snapshot"]["ms_catalog_status"] = "ready"
        bundle["snapshot"]["ms_needs"]["last_recommended_codes"] = [plans[0]["plan_code"]]
        code = plans[0]["plan_code"]
        choice = {"action": "recommend", "selected_plan_codes": [code], "follow_up": None, "reason": "定位指定套餐"}
        decision = self.run_node["product_decision"](bundle, plans, json.dumps(choice))
        self.assertEqual(decision["bundle"]["mode"], "lookup")
        ranked = self.run_node["rank"](decision["bundle"], plans, decision["shortlist_json"])
        self.assertEqual(ranked["error"], "")
        selection = {"reason": "核對指定套餐", "assessments": [
            {"plan_code": code, "requirements": [], "info_answers": [
                {"question_id": "Q1", "status": "unknown", "quote": "", "answer": "原文沒有辦理網址"},
            ]},
        ]}
        needs_json = self.run_node["decision_state"](bundle)["needs_json"]
        checked = self.run_node["validate_selection"](
            json.dumps(selection), ranked["sales_candidates_json"], needs_json,
        )
        self.assertEqual(checked["error"], "")
        reply = self.run_node["recommendation"](
            decision["bundle"], checked["recommendations_json"], "", "",
        )["bundle"]
        context = self.run_node["reply_context"](reply, message)
        self.assertEqual(self.product_link(context), {
            "frontend_action": "SHOW_PRODUCT_LINK", "service_type": "MS",
            "products": [{"plan_code": code, "link_label": "查看計劃"}],
        })
        no_action = {"frontend_action": "NONE", "service_type": "MS", "products": []}
        facts = self.merge("包唔包漫遊？", turn_intent="lookup", delivery_goal="facts", question_changes=[{
            "business": "MS", "operation": "add", "question_id": "",
            "text": "包唔包漫遊？", "evidence": ["包唔包漫遊？"],
        }])
        facts.update(mode="lookup", selected_plans=reply["selected_plans"])
        self.assertEqual(self.product_link(self.run_node["reply_context"](facts, "包唔包漫遊？")), no_action)
        for mode in ("availability", "interview", "request_address", "no_match", "error"):
            with self.subTest(mode=mode):
                context_for_mode = self.run_node["reply_context"]({**reply, "mode": mode}, message)
                self.assertEqual(self.product_link(context_for_mode), no_action)
        reply["selected_plans"].append({"plan_code": "PLAN_B", "markdown": "另一款計劃"})
        ambiguous = self.run_node["reply_context"](reply, message)
        self.assertIn("只問客戶想要邊款", ambiguous["instruction"])
        self.assertEqual(self.product_link(ambiguous), no_action)

    def test_whole_plan_fee_is_read_from_basic_information(self):
        plans = self.run_node["fetch_tags"]("XE", self.snapshot)["plans_tags"]
        self.snapshot["xe_needs"]["ready_to_recommend"] = True
        self.snapshot["xe_catalog_status"] = "ready"
        bundle = {"snapshot": self.snapshot, "business": "XE", "mode": "lookup"}
        shortlist = json.dumps({"selected_plan_codes": ["XEMXS1268AR"], "reason": "核對指定計劃"})
        result = self.run_node["rank"](bundle, plans, shortlist)
        self.assertEqual(result["error"], "")
        plan, = json.loads(result["sales_candidates_json"])
        self.assertIn("$78", plan["markdown"])
        self.assertEqual(plan["plan_monthly_fee"], "68")

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
                self.assertEqual(reply["instruction"], "")
                self.assertIn("包含日本漫遊", context["needs_json"])
