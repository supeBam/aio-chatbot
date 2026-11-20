import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AioWorkflowTests(unittest.TestCase):
    def setUp(cls):
        cls.flow = yaml.safe_load((ROOT / "dify" / "AIO Demo.txt").read_text(encoding="utf-8"))
        cls.graph = cls.flow["workflow"]["graph"]
        cls.nodes = {node["id"]: node["data"] for node in cls.graph["nodes"]}
        cls.functions = {}
        for node_id, data in cls.nodes.items():
            if data["type"] == "code":
                namespace = {}
                exec(compile(data["code"], node_id, "exec"), namespace)
                cls.functions[node_id] = namespace["main"]
        cls.catalog = cls.functions["catalog"]()
        cls.tags = json.loads(cls.catalog["allowed_tags_json"])

    def merge(self, changes, previous=None, message=""):
        return self.functions["merge"](
            json.dumps(changes), json.dumps(previous or {}),
            self.catalog["allowed_tags_json"], message
        )

    def rank(self, state):
        return json.loads(self.functions["rank"](
            json.dumps(state), self.catalog["plans_json"]
        )["candidates_json"])

    def test_catalog_matches_source_and_generates_whitelist(self):
        plans = json.loads((ROOT / "bn_plan_tags.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.catalog["plans_json"]), plans)
        self.assertEqual(self.tags, list(dict.fromkeys(tag for plan in plans for tag in plan["tag"])))
        for node_id in ("extract", "interview"):
            self.assertIn("{{#catalog.", json.dumps(self.nodes[node_id]))

    def test_readiness_is_not_a_tag_count(self):
        self.assertFalse(self.merge({"add": self.tags[:12], "recommend": False})["ready"])
        self.assertTrue(self.merge({"add": ["1000M"], "recommend": True})["ready"])
        self.assertTrue(self.merge({"recommend": True}, message="先推荐看看")["ready"])
        conditions = self.nodes["route"]["cases"][0]["conditions"]
        self.assertEqual(conditions[0]["variable_selector"], ["merge", "ready"])
        self.assertEqual(conditions[0]["varType"], "boolean")
        self.assertEqual(conditions[1]["variable_selector"], ["merge", "error"])
        self.assertNotIn("collected_count", json.dumps(self.nodes["route"]))

    def test_indifference_does_not_penalize_bundled_service(self):
        result = self.merge({"indifferent": ["Netflix"]}, {"collected_tags": ["1000M", "Netflix"]})
        self.assertFalse(result["error"])
        state = json.loads(result["state_json"])
        self.assertEqual(state["collected_tags"], ["1000M"])
        self.assertEqual(state["indifferent_tags"], ["Netflix"])
        self.assertEqual(set(state), {"collected_tags", "uninterested_tags", "indifferent_tags"})
        self.assertEqual(self.rank(state), self.rank({"collected_tags": ["1000M"]}))

    def test_scores_preferences_without_filtering_any_plan(self):
        codes = [plan["plan_code"] for plan in json.loads(self.catalog["plans_json"])[:3]]
        fixtures = [{"plan_code": codes[0], "tag": ["1000M"]},
                {"plan_code": codes[1], "tag": ["Netflix"]},
                {"plan_code": codes[2], "tag": ["10Gbps"]}]
        state = {"collected_tags": ["1000M", "1000M"], "uninterested_tags": ["10Gbps"],
                 "indifferent_tags": ["Netflix"]}
        response = self.functions["rank"](json.dumps(state), json.dumps(fixtures))
        plans = json.loads(response["candidates_json"])
        self.assertEqual([plan["plan_code"] for plan in plans], codes)
        self.assertEqual([plan["score"] for plan in plans], [3, 0, -3])
        self.assertEqual(plans[-1]["uninterested_matches"], ["10Gbps"])
        self.assertEqual(plans[-1]["unmatched_preferences"], ["1000M"])
        self.assertEqual(json.loads(response["request_json"]), state | {"collected_tags": ["1000M"]})
        self.assertTrue(self.rank({"collected_tags": ["Netflix", "100M"]}))

    def test_old_hard_constraints_migrate_to_soft_attitudes(self):
        previous = {"collected_tags": ["1000M", "Netflix"], "required_tags": ["Netflix"],
                    "not_needed_tags": ["家居电话"], "indifferent_tags": [],
                    "excluded_tags": ["10Gbps"]}
        result = self.merge({"recommend": True}, previous)
        self.assertFalse(result["error"])
        self.assertEqual(json.loads(result["state_json"]), {
            "collected_tags": ["1000M", "Netflix"], "uninterested_tags": ["10Gbps"],
            "indifferent_tags": ["家居电话"]})

    def test_corrections_deduplication_and_indifference(self):
        first = self.merge({"add": ["1000M", "1000M", "Netflix"]})
        state = json.loads(first["state_json"])
        self.assertEqual(state["collected_tags"], ["1000M", "Netflix"])
        second = self.merge({"indifferent": ["Netflix"]}, state)
        state = json.loads(second["state_json"])
        self.assertEqual(state["collected_tags"], ["1000M"])
        self.assertEqual(state["indifferent_tags"], ["Netflix"])
        third = self.merge({"uninterested": ["Netflix"]}, state)
        fourth = self.merge({"add": ["Netflix"]}, json.loads(third["state_json"]))
        state = json.loads(fourth["state_json"])
        self.assertEqual(state["uninterested_tags"], [])
        self.assertEqual(state["indifferent_tags"], [])
        removed = self.merge({"remove": ["Netflix"]}, state)
        self.assertNotIn("Netflix", json.loads(removed["state_json"])["collected_tags"])

    def test_invalid_extraction_preserves_state_and_blocks(self):
        previous = json.loads(self.merge({"add": ["1000M"]})["state_json"])
        invalid_outputs = ["bad json", '{"add":"Netflix"}', '{"unknown":[]}',
                           '{"add":["forged"]}', '{"recommend":"true"}',
                           '{"add":["Netflix"],"uninterested":["Netflix"]}',
                           '{"indifferent":["Netflix"],"uninterested":["Netflix"]}',
                           '{"add":["Netflix"],"remove":["Netflix"]}',
                           '{"require":["Netflix"]}', '{"exclude":["Netflix"]}']
        for invalid in invalid_outputs:
            with self.subTest(invalid=invalid):
                result = self.functions["merge"](invalid, json.dumps(previous),
                                                  self.catalog["allowed_tags_json"], "")
                self.assertEqual(json.loads(result["state_json"]), previous)
                self.assertFalse(result["ready"])
                self.assertTrue(result["error"])

    def test_clarification_blocks_even_when_recommend_requested(self):
        previous = json.loads(self.merge({"add": ["1000M"]})["state_json"])
        result = self.merge({"recommend": True, "clarification": "是否撤回原有网速要求？"}, previous)
        self.assertFalse(result["ready"])
        self.assertTrue(result["clarification"])
        for message in ("这些都不要", "除了宽带都不需要", "其他不用了"):
            result = self.merge({"uninterested": self.tags, "recommend": True,
                                 "clarification": "您指的是哪些服务？"}, previous, message)
            self.assertEqual(json.loads(result["state_json"]), previous)
            self.assertFalse(result["ready"])
            self.assertEqual(result["clarification"], "您指的是哪些服务？")

    def test_contextual_decline_is_not_forced_back_into_interview(self):
        previous = {"collected_tags": ["1000M", "24个月合约"],
                    "uninterested_tags": [], "indifferent_tags": []}
        for message in ("这些不需要啦", "這些不用了", "其他不用了", "除了宽带都不需要"):
            for recommend in (True, False):
                with self.subTest(message=message, recommend=recommend):
                    result = self.merge({"uninterested": ["家居电话"], "recommend": recommend},
                                        previous, message)
                    self.assertEqual(result["ready"], recommend)
                    self.assertFalse(result["error"])
                    self.assertFalse(result["clarification"])
                    self.assertEqual(json.loads(result["state_json"]),
                                     previous | {"uninterested_tags": ["家居电话"]})

    def test_prompts_describe_soft_preferences_without_guarantees(self):
        initial = json.loads(self.flow["workflow"]["conversation_variables"][0]["value"])
        self.assertEqual(set(initial), {"collected_tags", "uninterested_tags", "indifferent_tags"})
        extract = self.nodes["extract"]["prompt_template"][0]["text"]
        self.assertIn('"uninterested":[]', extract)
        self.assertNotIn('"require":', extract)
        self.assertNotIn('"exclude":', extract)
        for node_id in ("extract", "interview", "llm"):
            prompt = self.nodes[node_id]["prompt_template"][0]["text"]
            self.assertIn("不能承诺满足所有要求", prompt)
        recommendation = self.nodes["llm"]["prompt_template"][0]["text"]
        self.assertIn("比较符合偏好", recommendation)
        self.assertIn("uninterested_matches", recommendation)

    def test_interview_asks_flexible_catalog_questions_without_recommending(self):
        interview = self.nodes["interview"]
        prompt = interview["prompt_template"][0]["text"]
        self.assertIn("你没有推荐权限", prompt)
        self.assertIn("禁止展示具体套餐", prompt)
        self.assertIn("不可复制、续写或改编成推荐回复", prompt)
        self.assertIn("问题数量不设固定上限", prompt)
        self.assertIn("用户回答负担", prompt)
        self.assertNotIn("只问一个主问题", prompt)
        self.assertIn("具体标签选项必须来自本轮目录", prompt)
        self.assertIn("不同回答会怎样改变相关套餐的比较", prompt)
        self.assertIn("不换措辞重问同一主题", prompt)
        self.assertIn("{{#catalog.plans_json#}}", interview["prompt_template"][1]["text"])
        self.assertTrue(interview["memory"]["window"]["enabled"])
        self.assertEqual(self.nodes["interview_answer"]["answer"], "{{#interview.text#}}")

    def test_rank_explains_matches_and_recomputes_full_catalog(self):
        first = self.rank({"collected_tags": ["100M"]})
        second = self.rank({"collected_tags": ["10Gbps"]})
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertNotEqual(first[0]["plan_code"], second[0]["plan_code"])
        for plan in second[:3]:
            self.assertEqual(plan["matched_tags"], ["10Gbps"])
        self.assertEqual(len(second), 5)

    def test_candidates_include_exact_markdown_from_source(self):
        seen = set()
        for plan in json.loads(self.catalog["plans_json"]):
            candidates = self.rank({"collected_tags": plan["tag"]})
            self.assertLessEqual(len(candidates), 5)
            for candidate in candidates:
                code = candidate["plan_code"]
                self.assertEqual(candidate["markdown"], (ROOT / "data" / "bn" / f"{code}-cn").read_text(encoding="utf-8"))
                seen.add(code)
        self.assertEqual(seen, {plan["plan_code"] for plan in json.loads(self.catalog["plans_json"])})
        self.assertIn("{{#rank.candidates_json#}}", json.dumps(self.nodes["select"]))
        self.assertIn("{{#validate_selection.recommendations_json#}}", json.dumps(self.nodes["llm"]))

    def test_selection_can_rerank_but_only_choose_up_to_three_candidates(self):
        candidates = self.rank({"collected_tags": ["1000M"]})
        codes = [plan["plan_code"] for plan in candidates]
        selected = [codes[4], codes[1], codes[0]]
        result = self.functions["validate_selection"](
            json.dumps({"selected_plan_codes": selected, "reason": "比较正文中的条款"}), json.dumps(candidates))
        self.assertFalse(result["error"])
        self.assertEqual(json.loads(result["recommendations_json"]), [candidates[4], candidates[1], candidates[0]])
        for invalid in ("bad json", {"selected_plan_codes": ["forged"], "reason": ""},
                        {"selected_plan_codes": [codes[0], codes[0]], "reason": ""},
                        {"selected_plan_codes": codes[:4], "reason": ""},
                        {"selected_plan_codes": "wrong", "reason": ""},
                        {"selected_plan_codes": [codes[0]], "reason": [], "extra": True}):
            with self.subTest(invalid=invalid):
                result = self.functions["validate_selection"](
                    invalid if isinstance(invalid, str) else json.dumps(invalid), json.dumps(candidates))
                self.assertTrue(result["error"])
                self.assertEqual(json.loads(result["recommendations_json"]), [])
        for pool in (candidates[:1], []):
            result = self.functions["validate_selection"](
                json.dumps({"selected_plan_codes": [plan["plan_code"] for plan in pool], "reason": ""}), json.dumps(pool))
            self.assertFalse(result["error"])
            self.assertEqual(json.loads(result["recommendations_json"]), pool)

    def test_edges_selectors_and_code_outputs(self):
        self.assertEqual(len(self.nodes), len(self.graph["nodes"]))
        for edge in self.graph["edges"]:
            self.assertEqual(edge["data"]["sourceType"], self.nodes[edge["source"]]["type"])
            self.assertEqual(edge["data"]["targetType"], self.nodes[edge["target"]]["type"])
            if self.nodes[edge["source"]]["type"] == "if-else":
                handles = {case["case_id"] for case in self.nodes[edge["source"]]["cases"]} | {"false"}
                self.assertIn(edge["sourceHandle"], handles)
        for node_id, data in self.nodes.items():
            ancestors = set()
            pending = [node_id]
            while pending:
                target = pending.pop()
                for edge in self.graph["edges"]:
                    if edge["target"] == target and edge["source"] not in ancestors:
                        ancestors.add(edge["source"])
                        pending.append(edge["source"])
            selectors = re.findall(r"\{\{#(\w+)\.(\w+)#\}\}", json.dumps(data))
            selectors += [tuple(item["value_selector"]) for item in data.get("variables", [])
                          if "value_selector" in item]
            for producer, field in selectors:
                if producer not in {"sys", "conversation", "env"}:
                    self.assertIn(producer, ancestors)
                    self.assertIn(field, self.nodes[producer].get("outputs", {"text": None}))

    def test_question_is_saved_and_passed_to_extractor(self):
        assignments = self.nodes["save_question"]["items"]
        self.assertEqual(assignments[0]["value"], ["interview", "text"])
        self.assertEqual(assignments[0]["variable_selector"], ["conversation", "last_question"])
        self.assertIn("{{#conversation.last_question#}}", json.dumps(self.nodes["extract"]))
        self.assertTrue(self.nodes["extract"]["memory"]["window"]["enabled"])


if __name__ == "__main__":
    unittest.main()