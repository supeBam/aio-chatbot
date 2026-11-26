"""让后续选购沿用已确认条件，并区分失败与无匹配。"""

import copy
import json

if __package__:
    from .customer_turn_policy import replace
    from .model_output_schemas import MODEL_SCHEMAS
else:
    from customer_turn_policy import replace
    from model_output_schemas import MODEL_SCHEMAS


CONTINUATION_PROMPT = """- selection_context 区分新选购（new）、继续调整同一选购（continue）和要看其他套餐（alternatives）。已推荐后问「还有其他计划吗」表示继续找不同套餐，delivery_goal 用 selection。只有客户明确转话题或改目标才开始 new。
- 继续选购时，沿用已保存条件及 active_selection.turn_requirements。turn_conditions 只填写本轮的新条件，禁止用「其他」或历史原话重新创建原有条件。客户明确修改或撤回的临时条件，在 replaced_temporary_ids 填对应 T 编号。其他场景填空数组。
- 「不要某服务了」「唔加配」承接此前主动要求加配时，应 remove 原需求。明确要求套餐本身不能包括某服务时，才记录排除条件。

例：刚推荐过两款，客户只问「仲有冇其他計劃？」。沿用筛选条件并排除已经展示的计划。
{"changes":[],"question_changes":[],"clarification":"","reason":"继续看同一选购的其他计划","context_update":null,"turn_intent":"availability","delivery_goal":"selection","selection_context":"alternatives","replaced_temporary_ids":[],"link_request":"","availability_scope":"current_purchase","turn_conditions":[],"temporarily_excluded_ids":[],"purchase_switch":null}
"""


READ_CONTINUATION = r'''        selection_context = output.get("selection_context", "continue" if turn_intent == "shop" and saved_selection else "new")
        replaced = output.get("replaced_temporary_ids", [])
        if selection_context not in ("new", "continue", "alternatives") or not strings(replaced):
            raise ValueError("选购续接结构无效")
        switch = output.get("purchase_switch")
        current_quote = pending[-1] if pending else ""
        other_options = re.fullmatch(
            r"(?:(?:唔滿意|唔满意)[，, ]*|(?:除[咗了])[这這嗰][两兩二][个個款][，, ]*)?"
            r"(?:仲|還|还)?(?:請|请)?(?:有[冇无無]?|想[睇看]|再[睇看]|換|换)(?:有)?(?:啲|些|一[啲些]|冇|没)?"
            r"(?:其他|其它|另外)(?:plan|方案|計劃|计划|套餐|[啲個个款])*"
            r"(?:嗎|吗|嘛|呢|了|啦|[？?。！!\s])*",
            current_quote.strip(), re.I,
        )
        other_options = bool(other_options and not switch and (saved_selection or needs["last_recommended_codes"]))
        if other_options:
            selection_context = "alternatives"
            turn_intent, availability_scope, excluded_ids, link_request = "availability", "current_purchase", [], ""
        same_purchase = selection_context in ("continue", "alternatives") and not switch
        prior_temporary = (saved_selection or {}).get("turn_requirements", []) if same_purchase else []
        if replaced and (not same_purchase or not set(replaced) <= {item["id"] for item in prior_temporary}):
            raise ValueError("只能更换本次选购中已有的临时条件")
        if same_purchase and turn_intent == "availability" and output.get("delivery_goal") != "facts":
            availability_scope = "current_purchase"
'''


MERGE_CONTINUATION = '''        if selection_context == "alternatives" and same_purchase:
            delivery_goal = "selection"
            bundle["excluded_plan_codes"] = list(dict.fromkeys(
                (saved_selection or {}).get("shown_plan_codes", []) + needs["last_recommended_codes"]
            ))
        if same_purchase and delivery_goal == "selection":
            bundle["selection_context"] = selection_context
            if turn_intent == "availability":
                availability_scope = "current_purchase"
            saved_ids = {item["id"] for item in needs["requirements"]}
            excluded_ids = list(dict.fromkeys(excluded_ids + [
                identifier for identifier in (saved_selection or {}).get("temporarily_excluded_ids", [])
                if identifier in saved_ids
            ]))
            texts = {item["text"] for item in turn_requirements}
            inherited = [item for item in prior_temporary
                         if item["id"] not in replaced and item["text"] not in texts]
            turn_requirements = [
                {**item, "id": "T" + str(index)}
                for index, item in enumerate(inherited + turn_requirements, start=1)
            ]
'''


WITHDRAWAL_CODE = r'''def withdraws_optional_service(evidence):
    return any(re.fullmatch(
        r"(?!.*(?:必須|必须|包含|包括|連帶|连带|必带|必帶|[的嘅](?:套餐|[計计][劃划])))"
        r"(?:[不唔](?:想)?加[配購购].+|(?:不要|不需要|唔要|唔需要).+[了喇])",
        quote.strip(" ，,。！？!?"),
    ) for quote in evidence)


'''


OPTIONAL_WITHDRAWAL = '''                    if (field == "changes" and operation == "update" and existing
                            and existing.get("attitude") == "interested" and change["attitude"] == "uninterested"
                            and withdraws_optional_service(change["evidence"])):
                        operation = "remove"
'''


BOOKING_LIMITS = r'''def matches_monthly_limits(plan, requirements):
    for item in requirements:
        if item.get("priority") != "required" or item.get("attitude", "interested") != "interested":
            continue
        ceiling = re.fullmatch(
            r"(?:套餐)?(?:[參参]考)?月[費费](?:希望|必須|必须)?\s*"
            r"(低[於于過过]|少[於于過过]|不超[過过]|唔超[過过]|最多|上限(?:為|为)?)\s*"
            r"(?:HK)?\$\s*(\d+(?:\.\d+)?)", item.get("text", "").strip(),
        )
        if not ceiling:
            continue
        price = plan.get("plan_monthly_fee")
        if price is None:
            monthly = re.search(r"^套餐参考月费[：:]\s*(\d+(?:\.\d+)?)\s*$", plan["markdown"], re.M)
            price = monthly.group(1) if monthly else None
        if price is None:
            return False
        limit = float(ceiling.group(2))
        if float(price) > limit or (float(price) == limit and ceiling.group(1).startswith(("低", "少"))):
            return False
    return True


'''

TECHNICAL_REPLY = "今次未能完成計劃核對，請稍後再試。"


def apply_purchase_continuation(graph):
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}

    def edit(node_id, old, new):
        node = nodes[node_id]
        node["code"] = replace(node["code"], old, new)

    extract = nodes["extract"]
    extract["structured_output"]["schema"] = copy.deepcopy(MODEL_SCHEMAS["extract"])
    prompt = extract["prompt_template"][0]
    prompt["text"] = replace(prompt["text"], "## 本轮交付目标\n", "## 本轮交付目标\n" + CONTINUATION_PROMPT)
    lines = prompt["text"].splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('{"changes"') or "|" in line:
            continue
        try:
            example = json.loads(line)
        except json.JSONDecodeError:
            continue
        reason = example.get("reason", "")
        follows_purchase = example.get("turn_intent") == "shop" and (
            any(change["operation"] in ("update", "remove") for change in example.get("changes", []))
            or "上轮展示" in reason or "保留原" in reason
        )
        example.setdefault("selection_context", "continue" if follows_purchase else "new")
        example.setdefault("replaced_temporary_ids", [])
        lines[index] = json.dumps(example, ensure_ascii=False)
    prompt["text"] = "\n".join(lines)

    edit("catalog", '    view["purchase_archive"] = {',
         '    if "active_selection" in active:\n'
         '        view["active_selection"] = {"turn_requirements": active["active_selection"]["turn_requirements"]}\n'
         '    view["purchase_archive"] = {')

    for node_id in ("extract_validation", "merge"):
        edit(node_id, 'def main(llm_output, snapshot, business, event="message"):',
             WITHDRAWAL_CODE + 'def main(llm_output, snapshot, business, event="message"):')
        edit(node_id, '"catalog_recommendation", "delivery_goal"}',
             '"catalog_recommendation", "delivery_goal", "selection_context", "replaced_temporary_ids"}')
        edit(node_id, '        availability_scope = output.get("availability_scope", "catalog")\n',
             '        availability_scope = output.get("availability_scope", "catalog")\n' + READ_CONTINUATION)
        edit(node_id, '        if turn_intent == "lookup":',
             '''        if other_options and same_purchase:
            turn_requirements = []
        if turn_intent == "lookup":''')
        edit(node_id, '        if turn_intent == "availability" and not turn_requirements and not output["clarification"].strip():',
             '''        can_continue = same_purchase and (prior_temporary or needs["requirements"] or needs["last_recommended_codes"])
        if (turn_intent == "availability" and not turn_requirements and not can_continue
                and not output["clarification"].strip()):''')
        edit(node_id, '        switch = output.get("purchase_switch")\n        if switch is not None:',
             '        if switch is not None:')
        edit(node_id, '                    if operation == "add":',
             OPTIONAL_WITHDRAWAL + '                    if operation == "add":')
        edit(node_id, '        needs["pending_messages"] = []',
             '''        needs["requirements"] = [
            item for item in needs["requirements"]
            if not (item["attitude"] == "uninterested" and withdraws_optional_service(item["evidence"]))
        ]
        needs["pending_messages"] = []''')
        edit(node_id, '        if delivery_goal not in ("selection", "facts", "link"):',
             MERGE_CONTINUATION + '        if delivery_goal not in ("selection", "facts", "link"):')

    edit("decision_state", '    needs["link_requested"] = bool(bundle.get("link_request"))',
         '    needs["link_requested"] = bool(bundle.get("link_request"))\n'
         '    needs["excluded_plan_codes"] = bundle.get("excluded_plan_codes", [])')
    shortlist = nodes["shortlist"]["prompt_template"][0]
    shortlist["text"] = replace(shortlist["text"], "## 本轮回复动作",
                                "## 本轮回复动作\n- excluded_plan_codes 是客户本轮要求换掉的套餐，候选不得包含其中任何编号。继续选购时只能在有效条件内选套餐。")
    normalized = '''    follow_up = llm_output.get("follow_up")
    if isinstance(follow_up, dict) and follow_up.get("blocking") in ("true", "false"):
        llm_output = {**llm_output, "follow_up": {**follow_up, "blocking": follow_up["blocking"] == "true"}}
'''
    edit("shortlist_validation", "    llm_output = json.dumps(llm_output, ensure_ascii=False)",
         normalized + "    llm_output = json.dumps(llm_output, ensure_ascii=False)")
    for node_id in ("shortlist_validation", "product_decision"):
        edit(node_id, "        if business == \"BN\" and set(available) != set(snapshot[\"bn_scope_codes\"]):",
             '''        if set(selected).intersection(bundle.get("excluded_plan_codes", [])):
            raise ValueError("本轮要看其他套餐，候选不能重复上次展示的选项")
        if business == "BN" and set(available) != set(snapshot["bn_scope_codes"]):''')
    edit("rank", '        if any(code not in request["plan_codes"] for code in selected):',
         '''        if set(selected).intersection(bundle.get("excluded_plan_codes", [])):
            raise ValueError("本轮详情不能读取已排除的套餐")
        if any(code not in request["plan_codes"] for code in selected):''')
    for node_id in ("select_validation", "validate_selection"):
        edit(node_id, "def main(llm_output, candidates_json, needs_json):",
             BOOKING_LIMITS + "def main(llm_output, candidates_json, needs_json):")
        edit(node_id, 'if all(item["status"] == "supported" for item in assessed[code]\n                   if item["requirement_id"] in required_ids):',
             '''if (matches_monthly_limits(by_code[code], needs["requirements"])
                    and all(item["status"] == "supported" for item in assessed[code]
                            if item["requirement_id"] in required_ids)):''')

    edit("reply_context", 'or business == "BN" and code not in bundle["snapshot"]["bn_scope_codes"]):',
         '''or code in bundle.get("excluded_plan_codes", [])
                or business == "BN" and code not in bundle["snapshot"]["bn_scope_codes"]):''')
    edit("reply_context", '    if bundle["business"] == "BN" and presentation["goal"] == "selection" and mode in (',
         '    if bundle["business"] in BUSINESSES and presentation["goal"] == "selection" and mode in (')
    edit("reply_context", 'snapshot["bn_needs"]["active_selection"]',
         'snapshot[bundle["business"].lower() + "_needs"]["active_selection"]')
    edit("reply_context", '            "turn_intent": bundle.get("turn_intent", "shop"),',
         '''            "shown_plan_codes": list(dict.fromkeys(
                bundle.get("excluded_plan_codes", []) + presentation["plan_codes"]
            )),
            "turn_intent": bundle.get("turn_intent", "shop"),''')
    edit("ingress", '            snapshot["current_business"] = "BN"\n            snapshot["bn_scope_codes"] = []',
         '''            snapshot["current_business"] = "BN"
            if "active_selection" in snapshot["bn_needs"]:
                snapshot["bn_needs"]["active_selection"]["shown_plan_codes"] = []
            snapshot["bn_scope_codes"] = []''')
    edit("record_reply", 'if presentation and presentation["goal"] == "selection":',
         'if presentation and presentation["goal"] == "selection" and presentation["plan_codes"]:')
    edit("record_reply", 'elif mode in ("request_address", "error", "no_plans"):',
         'elif mode in ("request_address", "no_plans"):')

    route = nodes["form_route"]
    route["cases"].append({"case_id": "technical_error", "logical_operator": "and", "conditions": [{
        "id": "form_route-technical_error", "comparison_operator": "is", "value": "error",
        "varType": "string", "variable_selector": ["reply_context", "mode"],
    }]})
    answer = copy.deepcopy(next(node for node in graph["nodes"] if node["id"] == "answer"))
    answer["id"] = "technical_error_reply"
    answer["data"].update(answer=TECHNICAL_REPLY, title="提示本轮核对失败")
    for key in ("position", "positionAbsolute"):
        answer[key]["y"] += 340
    graph["nodes"].append(answer)
    for start, handle, end, source_type, target_type in (
        ("form_route", "technical_error", answer["id"], "if-else", "answer"),
        (answer["id"], "source", "product_link_action", "answer", "code"),
    ):
        graph["edges"].append({
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": source_type, "targetType": target_type},
            "id": f"{start}-{handle}-{end}-target", "source": start, "sourceHandle": handle,
            "target": end, "targetHandle": "target", "type": "custom", "zIndex": 0,
        })
