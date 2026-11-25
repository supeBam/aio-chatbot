"""让用户所见的套餐、办理入口和后续指代共用一份结果。"""

import copy
import json

if __package__:
    from .customer_turn_policy import replace
    from .model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
else:
    from customer_turn_policy import replace
    from model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT


DELIVERY_GOAL_PROMPT = """## 本轮交付目标
- delivery_goal 独立于 turn_intent 和需求保存范围。客户想按自己的条件找方案、比较后决定、找更实惠或更合适的套餐，用 selection。疑问句、临时条件和缺少「推介」字眼都不改变目标。
- 单纯查询覆盖、某项服务是否提供、或已指定套餐的事实，用 facts。明确索取唯一可定位套餐的办理入口，用 link，并按下节记录本轮原话。
- 地址回传和澄清承接待处理客户消息。地址事件说明和助手原话不代表客户改口。例：客户需要 1G 宽频并问有无较便宜的方案，用 selection；客户只问某款的合约期，用 facts。

"""

STORED_REQUEST = '''    pending = needs["pending_messages"]
    saved_selection = needs.get("active_selection")
    if business == "BN" and event == "address_change" and not pending and saved_selection:
        needs.update(questions=[], next_question_id=1)
        bundle = reply_bundle(snapshot, business, "analyze")
        bundle.update(clone(saved_selection), delivery_goal="selection")
        return {"bundle": bundle}'''

OUTPUT_GOAL = '''        delivery_goal = output.get("delivery_goal")
        if delivery_goal is None:
            delivery_goal = ("link" if turn_intent == "lookup" and link_request else
                             "selection" if turn_intent == "shop" or catalog_recommendation else "facts")
        if delivery_goal not in ("selection", "facts", "link"):
            raise ValueError("交付目标无效")
        if (delivery_goal == "selection" and turn_intent == "lookup"
                or delivery_goal == "link" and (turn_intent != "lookup" or not link_request)
                or delivery_goal != "link" and link_request):
            raise ValueError("交付目标与查询对象或客户原话不一致")
'''

PRESENTATION_CODE = r'''def present_plans(bundle):
    mode, business = bundle["mode"], bundle["business"]
    goal = bundle.get("delivery_goal")
    if not goal:
        goal = ("link" if mode == "lookup" and bundle.get("link_request") else
                "selection" if mode == "recommend" or bundle.get("catalog_recommendation") else "facts")
    plans = []
    seen = set()
    for plan in sales_plans(bundle["selected_plans"]):
        code = plan.get("plan_code")
        if (not valid_code(code) or code in seen
                or business == "BN" and code not in bundle["snapshot"]["bn_scope_codes"]):
            continue
        seen.add(code)
        plans.append(plan)
        if len(plans) == __RECOMMENDATION_LIMIT__:
            break
    if mode not in ("recommend", "availability", "lookup"):
        plans = []
    elif not plans:
        mode = "lookup_unavailable" if mode == "lookup" else "no_match"
    elif mode == "recommend" and goal == "facts":
        mode = "availability"
    selection = goal == "selection" and mode in ("recommend", "availability") and bool(plans)
    link = goal == "link" and mode == "lookup" and len(plans) == 1
    state = {"goal": goal, "mode": mode, "plan_codes": [plan["plan_code"] for plan in plans]}
    summary = ""
    if selection:
        headings = [
            f"{index}. {plan.get('display_name') or '計劃'}（{plan['plan_code']}）"
            for index, plan in enumerate(plans, start=1)
        ]
        summary = "今次揀咗以下計劃：\n\n" + "\n".join(headings) + "\n\n"
    products = build_link_products(plans) if selection or link else []
    state["link_status"] = ("ready" if products else
                            "ambiguous" if goal == "link" and mode == "lookup" else
                            mode if not plans else "not_requested")
    return state, plans, summary, products


'''

PRESENTATION_INSTRUCTION = '''    if plan_summary:
        instruction += (
            "\\n按推薦版式承接「今次揀咗」同有序套餐名單，名單含名稱及 {plan_code}。"
            "直接按同一順序用「第 1 款」「第 2 款」講已核實嘅計劃內容、參考月費、合約同重要取捨。"
            "毋須重列名單，亦唔將精選數目講成全目錄可售總數。"
        )
'''


def apply_recommendation_delivery(graph):
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}

    def edit(node_id, old, new):
        node = nodes[node_id]
        node["code"] = replace(node["code"], old, new)

    extract = nodes["extract"]
    extract["structured_output"]["schema"] = copy.deepcopy(MODEL_SCHEMAS["extract"])
    prompt = extract["prompt_template"][0]
    start = prompt["text"].index("## 本轮目录推荐")
    stop = prompt["text"].index("## 本轮办理链接请求", start)
    prompt["text"] = prompt["text"][:start] + DELIVERY_GOAL_PROMPT + prompt["text"][stop:]
    lines = prompt["text"].splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('{"changes"') or "|" in line:
            continue
        try:
            example = json.loads(line)
        except json.JSONDecodeError:
            continue
        example.pop("catalog_recommendation", None)
        example["delivery_goal"] = ("link" if example.get("link_request") else
                                    "selection" if example.get("turn_intent") == "shop" else "facts")
        lines[index] = json.dumps(example, ensure_ascii=False)
    prompt["text"] = "\n".join(lines)

    for node_id in ("extract_validation", "merge"):
        edit(node_id, '"link_request", "catalog_recommendation"}',
             '"link_request", "catalog_recommendation", "delivery_goal"}')
        code = nodes[node_id]["code"].replace("def main(llm_output, snapshot, business):",
                                             'def main(llm_output, snapshot, business, event="message"):')
        nodes[node_id]["code"] = code
        edit(node_id, '    pending = needs["pending_messages"]', STORED_REQUEST)
        edit(node_id, '        bundle.update(turn_intent=turn_intent, availability_scope=availability_scope,',
             OUTPUT_GOAL + '        bundle.update(delivery_goal=delivery_goal, turn_intent=turn_intent, availability_scope=availability_scope,')
        nodes[node_id]["variables"].append({
            "value_selector": ["ingress", "event"], "value_type": "string", "variable": "event",
        })
    edit("extract_validation", "    result = validate(llm_output, snapshot, business)",
         "    result = validate(llm_output, snapshot, business, event)")

    for node in nodes.values():
        if "def validate_snapshot(" in node.get("code", ""):
            allowed = 'set(needs) - {"purchase_archive", "purchase_label"}'
            if allowed in node["code"]:
                node["code"] = replace(node["code"], allowed,
                                       'set(needs) - {"purchase_archive", "purchase_label", "active_selection"}')
            else:
                node["code"] = replace(node["code"], "set(needs) != set(NEEDS_DEFAULT)",
                                       'set(needs) - {"active_selection"} != set(NEEDS_DEFAULT)')
            node["code"] = replace(node["code"], '    view = clone(needs)\n',
                                   '    view = clone(needs)\n    view.pop("active_selection", None)\n')
    edit("catalog", 'if key != "purchase_archive"}', 'if key not in ("purchase_archive", "active_selection")}')
    edit("ingress", '        else:\n            snapshot["current_business"] = "BN"',
         '''        else:
            if snapshot["current_business"] != "BN":
                snapshot["bn_needs"].pop("active_selection", None)
            snapshot["current_business"] = "BN"''')

    for node_id in ("shortlist_validation", "product_decision"):
        edit(node_id, '        if turn_intent == "lookup" and len(selected) > 2:',
             r'''        ordinal = re.search(r"第\s*([12一二兩两])\s*[款個个]", bundle.get("link_request", ""))
        if turn_intent == "lookup" and action == "recommend" and ordinal:
            index = 0 if ordinal.group(1) in ("1", "一") else 1
            previous = needs["last_recommended_codes"]
            expected = previous[index:index + 1]
            if expected and expected[0] in available and selected != expected:
                raise ValueError("办理入口必须使用上次展示顺序中的指定套餐")
            if not expected or expected[0] not in available:
                selected = []
        if turn_intent == "lookup" and len(selected) > 2:''')

    context = nodes["reply_context"]
    code = context["code"]
    start = code.index("def build_link_products(")
    stop = code.index("    products = []", start)
    code = code[:start] + "def build_link_products(plans):\n" + code[stop:]
    code = replace(code, "def main(bundle, message):",
                   PRESENTATION_CODE.replace("__RECOMMENDATION_LIMIT__", str(RECOMMENDATION_LIMIT))
                   + "def main(bundle, message):")
    code = replace(code, '    snapshot = bundle["snapshot"]',
                   '''    snapshot = clone(bundle["snapshot"])
    presentation, plans, plan_summary, products = present_plans(bundle)
    if bundle["business"] == "BN" and presentation["goal"] == "selection" and bundle["mode"] in (
            "recommend", "availability", "no_match"):
        snapshot["bn_needs"]["active_selection"] = {
            "turn_intent": bundle.get("turn_intent", "shop"),
            "availability_scope": bundle.get("availability_scope", "catalog"),
            "turn_requirements": bundle.get("turn_requirements", []),
            "temporarily_excluded_ids": bundle.get("temporarily_excluded_ids", []),
        }''')
    start = code.index('    link_request = bundle.get("link_request", "")')
    stop = code.index("    return {", start)
    code = code[:start] + '''    if presentation["goal"] == "link" and bundle["mode"] == "lookup":
        if products:
            instruction += "\\n客戶想要指定計劃嘅辦理入口，請自然回應。入口由前端展示。"
        else:
            instruction += "\\n如果有多款可能指代，只問客戶想要邊款。"
''' + PRESENTATION_INSTRUCTION + code[stop:]
    code = replace(code, 'dump(sales_plans(bundle["selected_plans"]))', "dump(plans)")
    code = replace(code, '"business": business, "mode": bundle["mode"]',
                   '"business": business, "mode": presentation["mode"]')
    code = replace(code, 'build_link_products(bundle["mode"], link_requested, catalog_requested, bundle["selected_plans"])',
                   "products,\n        \"presentation\": presentation,\n        \"plan_summary\": plan_summary")
    main_start = code.index("def main(bundle, message):")
    main = code[main_start:].replace('bundle["mode"]', "mode")
    main = replace(main, "    presentation, plans, plan_summary, products = present_plans(bundle)\n",
                   "    presentation, plans, plan_summary, products = present_plans(bundle)\n"
                   '    mode = presentation["mode"]\n')
    context["code"] = code[:main_start] + main
    context["outputs"].update(
        presentation={"children": None, "type": "object"},
        plan_summary={"children": None, "type": "string"},
    )
    nodes["answer"]["answer"] = "{{#reply_context.plan_summary#}}" + nodes["answer"]["answer"]

    reply = nodes["reply"]["prompt_template"][0]
    for prefix, line in (
        ("- 分段時，每款用 Markdown 五級標題", "- 若 instruction 已提供有序套餐名單，正文按名單順序交代每款內容同條件。每款可用「第 1 款」「第 2 款」作小標。"),
        ("- 推薦兩款時預設簡短分段", "- 推薦兩款時，用簡短分段比較已核實嘅規格、月費、合約同重要限制。"),
    ):
        original = next((item for item in reply["text"].splitlines() if item.startswith(prefix)), "")
        if original:
            reply["text"] = replace(reply["text"], original, line)
    reply["text"] = replace(reply["text"], "## 输出契约",
                           "## 输出契约\n- 依照 instruction 使用已先展示嘅有序名單，避免再次報讀套餐名稱同編號。")
    reply["text"] = replace(reply["text"], "提到某款時附上原有計劃編號",
                           "冇有序名單時，提到某款要附上原有計劃編號")

    record = nodes["record_reply"]
    edit("record_reply", "interview_topic, products=None):",
         'interview_topic, presentation=None, plan_summary=""):')
    start = record["code"].index('        if mode == "availability":')
    stop = record["code"].index('        if needs["interview_pending"]', start)
    record["code"] = record["code"][:start] + '''        if presentation and presentation["goal"] == "selection":
            needs["last_recommended_codes"] = presentation["plan_codes"]
        elif presentation is None and mode == "availability":
            shown_codes = [
                plan["plan_code"] for plan in json.loads(selected_plans_json)
                if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(plan["plan_code"]) + r"(?![A-Za-z0-9_-])", text)
            ]
            if shown_codes:
                needs["last_recommended_codes"] = shown_codes
''' + record["code"][stop:]
    edit("record_reply", '        if mode == "recommend":', '        if mode == "recommend" and presentation is None:')
    edit("record_reply", '        needs["last_reply"] = text', '        needs["last_reply"] = plan_summary + text')
    record["variables"] = [v for v in record["variables"] if v["variable"] != "products"]
    record["variables"].append({
        "value_selector": ["reply_context", "presentation"], "value_type": "object", "variable": "presentation",
    })
    record["variables"].append({
        "value_selector": ["reply_context", "plan_summary"], "value_type": "string", "variable": "plan_summary",
    })
