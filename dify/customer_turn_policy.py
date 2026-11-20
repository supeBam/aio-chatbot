"""Apply the sales conversation policy to the structured Dify chatflow."""

import json

if __package__:
    from .model_output_schemas import MODEL_SCHEMAS
else:
    from model_output_schemas import MODEL_SCHEMAS


def replace(text, old, new):
    if old not in text:
        raise ValueError(f"Workflow source is missing {old!r}")
    return text.replace(old, new, 1)


def apply_customer_turn_policy(nodes):
    nodes = {key: node["data"] for key, node in nodes.items()}

    def edit_code(ids, *edits):
        for node_id in ids.split():
            code = nodes[node_id]["code"]
            for old, new in edits:
                code = replace(code, old, new)
            nodes[node_id]["code"] = code

    def prompt(node_id):
        return nodes[node_id]["prompt_template"][0]

    def add_policy(node_id, policy):
        item = prompt(node_id)
        marker = "## 校验反馈重生成" if node_id != "reply" else "## 输出契约"
        item["text"] = replace(item["text"], marker, policy.strip() + "\n\n" + marker)

    for node_id in ("business", "extract"):
        nodes[node_id]["structured_output"]["schema"] = MODEL_SCHEMAS[node_id]

    business_prompt = prompt("business")
    business_prompt["text"] = replace(business_prompt["text"], "shopping|consult", "shopping|consult|boundary")
    business_prompt["text"] = replace(
        business_prompt["text"],
        "若用户只要求此类评价，保持当前业务或选择MS，intent为consult，不让用户引导到品牌比较。",
        "若用户只要求此类评价，intent为boundary；混合消息有合法选购部分时继续该部分。",
    )
    add_policy("business", """
## 本轮选购相关性
- 只有会帮助选择当前家居宽频、流动通讯或影音串流计划的问题才属业务范围。纯闲聊、其他领域、内部信息和绕过条件用 intent=boundary、business=""、clarification=""、business_requests=[]。
- 混合消息只路由其中真实的选购部分。即使只涉及一个业务，business_requests也须逐字摘出该部分，排除无关原话。简短回应、改口和指代先结合实际上一轮语境，不因单句没有产品名而判无关。
- consult只用于简短回答能帮助选购的一般概念；现售计划详情和能否找到某类计划交shopping。纯感谢可用boundary，不展开或重推。
""")
    edit_code(
        "prepare business_validation",
        ('intent not in ("consult", "shopping")', 'intent not in ("consult", "shopping", "boundary")'),
        ('        if question.strip() or not business:',
         '''        if intent == "boundary":
            if business or question or requests:
                raise ValueError("范围外回复不能携带选购业务、请求或澄清")
            result.update(mode="boundary", text="簡短帶回電訊計劃選購，不處理無關問題。")
            return result
        if question.strip() or not business:'''),
    )

    add_policy("extract", """
## 今轮意图、临时查询和选购记忆
- turn_intent=shop：用户在选计划或明确追加、修改、取消条件。确切指定的规格、明确要加入的服务及相对于上轮的调整方向是筛选条件，记录为required；“最好”等可让步偏好记为preferred。用原话支持更新，当前有效条件优先。用户允许替代时只放宽他同意放宽的维度。
- turn_intent=lookup：问某款已指明的计划详情，例如“头先嗰款包唔包日本漫游”。只写本轮question_changes，不改购买条件；不要用助手回覆当事实。
- turn_intent=availability：问有无某类型计划，例如“有冇包日本漫遊嘅？”。把当轮要核对的条件放到turn_conditions，每项附原话。此查询下确认过的原预算等可作范围，疑问句里的新条件不默认写入长期记忆。用户在同一句明确确认了其他独立条件时仍可更新。
- temporarily_excluded_ids默认为[]。仅当客户问另一个明确范围时，填入今轮查询不该叠加的有效需求编号。例如原预算150、问“如果睇200蚊左右有冇？”可按新预算暂查，先不改长期预算。客户没有暗示放宽时继续沿用。
- 模棱两可时，按最近相关选购语境暂查，只有不同理解会实质改变答案且不能先作有用回应时，才用clarification注明唯一需要问的区别。暂时推断不写入changes或context_update。若上轮在澄清，结合原先问题理解这次短答案。
- purchase_switch默认为null。同一业务中明确另开选购时用{"target":"new","evidence":["用户原话"]}；重回已保存的选购时target为purchase_archive里的编号。按当前客户语句识别服务对象，单纯加一项条件不切换。新选购只新增条件；回到已保存选购时以该对象的需求编号更新。
- 本轮问题只用question_changes中的add，处理完便不再视为当前问题。只有明示的购买条件写入changes。只问计划详情时可照样回答，即使该计划不合适。
- 输出必须带turn_intent、turn_conditions、temporarily_excluded_ids、purchase_switch。临时字段仅用于availability；没有消息的地址事件沿用当前选购，四个字段分别为shop、[]、[]、null。

例：当前在选每月$150以内的上台计划；用户说“有冇包日本漫遊嘅？”。沿用预算暂查，保留长期条件。
{"changes":[],"question_changes":[],"clarification":"","reason":"沿用当前预算临时核对有无日本漫游","context_update":null,"turn_intent":"availability","turn_conditions":[{"text":"包含日本漫游","evidence":["有冇包日本漫遊嘅？"]}],"temporarily_excluded_ids":[],"purchase_switch":null}

例：上轮刚展示两款上台计划；用户说“頭先嗰款包唔包日本漫遊？”。交下游定位可能指代的计划和核实，不新增购买条件。
{"changes":[],"question_changes":[{"business":"MS","operation":"add","question_id":"","text":"用户指的上一轮计划是否包括日本漫游","evidence":["頭先嗰款包唔包日本漫遊？"]}],"clarification":"","reason":"核对上一轮计划详情","context_update":null,"turn_intent":"lookup","turn_conditions":[],"temporarily_excluded_ids":[],"purchase_switch":null}
""")
    item = prompt("extract")
    lines = item["text"].splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('{"changes"') or '|' in line:
            continue
        example = json.loads(line)
        if "turn_intent" in example:
            continue
        example.update(turn_intent="shop", turn_conditions=[], temporarily_excluded_ids=[], purchase_switch=None)
        for change in example["changes"]:
            change.setdefault("priority", "required" if change["attitude"] == "interested" else "unspecified")
        lines[index] = json.dumps(example, ensure_ascii=False)
    item["text"] = "\n".join(lines)

    # The existing conversation variables still store one active purchase per business.
    # Archived purchases live alongside that active purchase and are read only on a switch.
    edit_code(
        "ingress",
        ('set(needs) != set(NEEDS_DEFAULT):',
         'set(needs) - {"purchase_archive", "purchase_label"} != set(NEEDS_DEFAULT):'),
        ('        if not isinstance(needs["requirements"], list)',
         '''        archive = needs.get("purchase_archive", {})
        if not isinstance(needs.get("purchase_label", ""), str) or not isinstance(archive, dict):
            raise ValueError("选购记忆结构无效")
        for key, saved in archive.items():
            if (not re.fullmatch(r"S[1-9][0-9]*", key) or not isinstance(saved, dict)
                    or set(saved) != set(NEEDS_DEFAULT) | {"purchase_label"}
                    or not isinstance(saved["purchase_label"], str)):
                raise ValueError("已保存的选购结构无效")
        if not isinstance(needs["requirements"], list)'''),
        ('            snapshot["bn_needs"].update(',
         '''            for saved in snapshot["bn_needs"].get("purchase_archive", {}).values():
                saved.update(last_reply="", last_recommended_codes=[], interview_pending=False, last_interview_topic="")
            snapshot["bn_needs"].update('''),
    )

    # Expose archived scopes to extraction only. Downstream product selection uses the active purchase.
    edit_code(
        "catalog",
        ('    return {\n        "snapshot": snapshot, "process":',
         '''    active = snapshot[prefix + "_needs"]
    view = {key: value for key, value in active.items() if key != "purchase_archive"}
    view["purchase_archive"] = {
        key: {"label": saved["purchase_label"], "requirements": saved["requirements"]}
        for key, saved in active.get("purchase_archive", {}).items()
    }
    return {
        "snapshot": snapshot, "process":'''),
        ('"needs_json": dump(snapshot[prefix + "_needs"])', '"needs_json": dump(view)'),
    )
    edit_code(
        "merge extract_validation",
        ('"reason", "readiness", "context_update"}:',
         '"reason", "readiness", "context_update", "turn_intent", "turn_conditions", "temporarily_excluded_ids", "purchase_switch"}:'),
        ('        errors = []\n',
         '''        turn_intent = output.get("turn_intent", "shop")
        conditions = output.get("turn_conditions", [])
        excluded_ids = output.get("temporarily_excluded_ids", [])
        if turn_intent not in ("shop", "lookup", "availability"):
            raise ValueError("本轮意图无效")
        if not isinstance(conditions, list) or len(conditions) > 20 or (conditions and turn_intent != "availability"):
            raise ValueError("本轮查找条件无效")
        if not strings(excluded_ids) or (excluded_ids and (not conditions or turn_intent != "availability")):
            raise ValueError("本轮临时排除条件无效")
        turn_requirements = []
        for index, item in enumerate(conditions, start=1):
            if (not isinstance(item, dict) or set(item) != {"text", "evidence"}
                    or not isinstance(item["text"], str) or not item["text"].strip()
                    or operator_reference(item["text"])):
                raise ValueError("临时查询条件无效")
            verify_evidence(item["evidence"])
            turn_requirements.append({"id": "T" + str(index), "text": item["text"],
                                      "evidence": item["evidence"], "attitude": "interested",
                                      "priority": "required"})
        if turn_intent == "lookup":
            if output["changes"] or output["context_update"] is not None:
                raise ValueError("查询指定计划不得更新选购条件")
            if not output["question_changes"] and not output["clarification"].strip():
                raise ValueError("查询指定计划必须提供本轮问题或待澄清问题")
        if any(not isinstance(item, dict) or item.get("operation") != "add"
               for item in output["question_changes"]) and "turn_intent" in output:
            raise ValueError("本轮问题只使用新增操作")
        if turn_intent == "availability" and not turn_requirements and not output["clarification"].strip():
            raise ValueError("查找某类计划必须提供本轮条件或待澄清问题")
        switch = output.get("purchase_switch")
        if switch is not None:
            if not isinstance(switch, dict) or set(switch) != {"target", "evidence"}:
                raise ValueError("选购切换结构无效")
            verify_evidence(switch["evidence"])
            archive = needs.get("purchase_archive", {})
            target = switch["target"]
            if not isinstance(target, str) or (target != "new" and target not in archive):
                raise ValueError("选购切换目标无效")
            archived_id = "S" + str(max((int(key[1:]) for key in archive), default=0) + 1)
            saved = {key: needs[key] for key in NEEDS_DEFAULT}
            saved.update(purchase_label=needs.get("purchase_label", "原有選購"),
                         pending_messages=[], questions=[], next_question_id=1,
                         ready_to_recommend=False)
            current = clone(NEEDS_DEFAULT) if target == "new" else archive.pop(target)
            archive[archived_id] = saved
            if target == "new":
                current["purchase_label"] = " ".join(switch["evidence"])
            current.update(purchase_archive=archive, pending_messages=pending)
            snapshot[business.lower() + "_needs"] = needs = current
        if not set(excluded_ids).issubset({item["id"] for item in needs["requirements"]}):
            raise ValueError("临时排除只能引用当前选购的已保存需求")
        if "turn_intent" in output:
            needs.update(questions=[], next_question_id=1)
        errors = []
'''),
        ('        answered_interview = bool(pending)',
         '        answered_interview = turn_intent == "shop" and bool(pending)'),
        ('        needs["interview_pending"] = False\n        if errors:',
         '        if turn_intent == "shop":\n            needs["interview_pending"] = False\n        if errors:'),
        ('        return {"bundle": reply_bundle(snapshot, business, "analyze", output["clarification"].strip())}',
         '''        bundle = reply_bundle(snapshot, business, "analyze", output["clarification"].strip())
        bundle.update(turn_intent=turn_intent, turn_requirements=turn_requirements, temporarily_excluded_ids=excluded_ids)
        return {"bundle": bundle}'''),
    )

    edit_code(
        "decision_state",
        ('    by_code = ', '''    needs.pop("purchase_archive", None)
    needs["turn_intent"] = bundle.get("turn_intent", "shop")
    if needs["turn_intent"] == "lookup":
        needs["requirements"] = []
    excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
    needs["requirements"] = [item for item in needs["requirements"] if item["id"] not in excluded_ids]
    needs["requirements"].extend(bundle.get("turn_requirements", []))
    by_code = '''),
    )

    item = prompt("shortlist")
    obsolete = (
        "- 用户要求的精确速度", "- 有同业务可售套餐", "- questions是需要回答",
        "- 查询不自动成为", "- priority=required尊重", "- 确无合理改善",
        "- 不擅自把相对偏好", "- 首次需求示例", "- 用户目标规格无精确",
        "- 用户未声明不可超出", "- 不要求直接选择最快", "- 提取阶段的待澄清信息非空",
        "- 最多3次可选访谈", "- selected_plan_codes在追问时也保留",
    )
    item["text"] = "\n".join(line for line in item["text"].splitlines() if not line.startswith(obsolete))
    item["text"] = replace(
        item["text"],
        '{"action":"recommend","selected_plan_codes":["DEMO_BN_1000"],"follow_up":null,"reason":"本轮可售目录没有比上轮1000M更快的候选，仅保留未改善备选，必须披露带宽没有提升"}',
        '{"action":"recommend","selected_plan_codes":[],"follow_up":null,"reason":"目录未发现更高速计划，继续原文核对，不把旧计划作为替代推荐"}',
    )
    item["text"] = replace(
        item["text"],
        '用户未声明拒绝比较超预算方案。',
        '用户已确认80元为预算上限。',
    )
    item["text"] = replace(
        item["text"],
        '{"action":"recommend","selected_plan_codes":["DEMO_BN_2000"],"follow_up":null,"reason":"2000M相对上轮1000M有提升，但378元超出80元预算；保留作有缺口的比较，不能因98元较便宜就只返回1000M并称其更快"}',
        '{"action":"recommend","selected_plan_codes":[],"follow_up":null,"reason":"标签中没有同时更快且符合预算的计划，继续核对当前可售原文"}',
    )
    add_policy("shortlist", """
## 本轮回复动作
- needs_json.turn_intent=lookup时，只定位用户问的计划，用recommend进入事实核对，selected_plan_codes只放明确指代或至多两个合理的当前可售对象。两个对象的结论一样就直接答；不能定位可核对的对象时才clarify。禁止顺带挑其他计划。
- availability用recommend进入全目录核对，此轮只回答是否有相符计划。shop只有能帮助当前选购时才用recommend。除lookup外，编号列表只是优先核对顺序；空列表仍会检查当前可售原文。
- 相关问答尽量先答重点。追问每轮只问一个会改变选择的问题，不因轮数到了就推不符合的计划。
- 用户要求的规格、必须服务和比较方向都须核实；未知或没有改善的旧计划不能作为推荐兜底。客人允许看替代时按他明确放宽的条件重新核对，其他限制继续有效。
- 历史答问用于理解指代和避免重问，不能借历史补回已撤回条件，不从其他选购读取条件。
""")

    edit_code(
        "product_decision shortlist_validation",
        ('        if bundle["instruction"] and action != "clarify":',
         '''        can_check = bool(bundle.get("turn_requirements")) or (
            bundle.get("turn_intent") == "lookup" and bool(selected)
        )
        if bundle["instruction"] and action != "clarify" and not (action == "recommend" and can_check):'''),
        ('        if action == "recommend":\n',
         '''        turn_intent = bundle.get("turn_intent", "shop")
        if action == "interview" and turn_intent in ("lookup", "availability"):
            raise ValueError("查询应直接核对；只有对象或结果范围歧义才能澄清")
        if action == "recommend":
'''),
        ('''            if not follow_up["blocking"] and (needs["interview_rounds"] >= 3 or follow_up["topic"] in needs["asked_topics"]):
                action = "recommend"
            elif follow_up["topic"] in needs["asked_topics"] and not bundle["instruction"]:
''',
         '''            if follow_up["topic"] in needs["asked_topics"] and not bundle["instruction"]:
'''),
        ('            if not (needs["requirements"] or needs["questions"] or needs["user_context"] or needs["interview_rounds"]):',
         '''            if turn_intent == "shop" and not (needs["requirements"] or needs["questions"] or needs["user_context"] or needs["interview_rounds"] or needs["answer_history"]):
'''.rstrip()),
        ('            bundle.update(mode="recommend", instruction="当轮展示已核对的初步比较；披露预算、条件及未知信息的缺口，不等待额外确认。", interview_topic="")',
         '''            mode = "recommend" if turn_intent == "shop" else turn_intent
            bundle.update(mode=mode, instruction="", interview_topic="")'''),
    )

    route = nodes["recommend_route"]["cases"][0]
    route["logical_operator"] = "or"
    for mode in ("lookup", "availability"):
        route["conditions"].append({**route["conditions"][0], "id": "details-" + mode, "value": mode})
    edit_code(
        "rank",
        ('        request.update(selected_plan_codes=selected, shortlist_reason=output["reason"])',
         '''        if bundle["mode"] != "lookup":
            prioritized = set(selected)
            selected += [code for code in request["plan_codes"] if code not in prioritized]
        request.update(selected_plan_codes=selected, shortlist_reason=output["reason"])'''),
        ('if not isinstance(candidates, list) or len(candidates) > 5:',
         'if not isinstance(candidates, list) or len(candidates) != len(selected):'),
        ('后端候选必须为最多五款套餐的列表', '后端结果必须覆盖所有请求的计划'),
        ('"questions": sales_needs(needs)["questions"], "limit": 5',
         '"questions": sales_needs(needs)["questions"], "limit": len(plans_tags)'),
    )
    nodes["rank"]["title"] = "按本輪查詢批量取得可售計劃原文"

    item = prompt("select")
    item["text"] = "\n".join(
        line for line in item["text"].splitlines()
        if not line.startswith(("- 候选中存在原文", "- 仅有未改善"))
    )
    # Remove examples that teach returning a non-matching or unverified plan.
    lines = item["text"].splitlines()
    for index, line in enumerate(lines):
        if line.startswith('{"assessments"') and ('"status": "unsupported"' in line or '"plan_code": "DEMO_BN_2000"' in line and '"status": "unknown"' in line):
            lines[index] = '{"assessments":[],"reason":"没有核实到符合本轮要求的计划"}'
    item["text"] = "\n".join(lines)
    item["text"] = replace(item["text"], "仅有未改善备选时如实标记缺口，不为保留原推荐伪造支持。", "未改善的旧计划不入选。")
    add_policy("select", """
## 核对与本轮结果
- 选购及查有无时，候选包含当前可售范围的完整原文，按本轮条件核对；优先顺序不限制搜索，检查相关计划，不能只检查开头几项。
- shop和availability只返回所有priority=required条件均有原文证据支持的计划。临时T编号与R编号同样需要核对。找不到就返回空assessments。preferred用来排序，不默许放宽其他条件。
- lookup只核对用户所指计划的问题，回复是否符合购买条件不是此轮任务。不要用其他计划替换被问对象。
""")

    edit_code(
        "select_validation validate_selection",
        ('        requirements = {item["id"] for item in needs["requirements"]}',
         '''        requirements = {item["id"] for item in needs["requirements"]}
        required_ids = {item["id"] for item in needs["requirements"]
                        if item.get("priority") == "required"}
        eligible = []'''),
        ('            answered[code] = info_answers\n',
         '''            answered[code] = info_answers
            if all(item["status"] == "supported" for item in assessed[code]
                   if item["requirement_id"] in required_ids):
                eligible.append(code)
'''),
        ('for code in selected\n', 'for code in eligible\n'),
        ('        result["recommendations_json"] = dump([',
         '''        if needs.get("turn_intent") == "lookup" and set(eligible) != set(by_code):
            raise ValueError("查询必须核对全部指定对象，不能任意漏掉其中一款")
        result["recommendations_json"] = dump(['''),
    )
    edit_code(
        "recommendation",
        ('        bundle["selected_plans"] = json.loads(recommendations_json)',
         '''        plans = json.loads(recommendations_json)
        bundle["selected_plans"] = plans
        mode = bundle["mode"]
        if mode == "lookup" and not plans:
            bundle.update(mode="lookup_unavailable", instruction="而家未能核實你問嗰款計劃。")
        elif mode in ("recommend", "availability") and not plans:
            bundle.update(mode="no_match", instruction="我哋暫時冇符合你今次要求嘅計劃。")'''),
    )
    edit_code(
        "reply_context",
        ('    if bundle["mode"] == "error":',
         '''    needs.pop("purchase_archive", None)
    if bundle["mode"] in ("availability", "no_match", "lookup_unavailable"):
        excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
        current = [item for item in needs["requirements"] if item["id"] not in excluded_ids]
        needs = {"requirements": current + bundle.get("turn_requirements", [])}
    if bundle["mode"] == "error":'''),
    )
    edit_code(
        "record_reply",
        ('    if persist_reply and business:', '    if persist_reply and business and mode != "boundary":'),
        ('        needs["last_reply"] = text',
         '''        needs.update(questions=[], next_question_id=1)
        if needs["interview_pending"] and mode in ("lookup", "lookup_unavailable", "availability", "no_match"):
            return {"snapshot": snapshot}
        needs["last_reply"] = text'''),
    )

    item = prompt("reply")
    item["text"] = replace(
        item["text"], "no_plans：说明查询成功但暂无套餐，可建议更换地址或业务；不虚构套餐。",
        "no_plans：直接說今次查詢暫時冇可選計劃，答完就停。",
    )
    item["text"] = replace(
        item["text"],
        "原方案若展示只能作为未改善的备选；候选池没有改善不等于整个可售目录没有，不推断地址最高能力。",
        "唔補推舊計劃，亦唔推斷地址最高網速。",
    )
    add_policy("reply", """
## 像香港電訊顧問咁答
- 每輪只講有助揀計劃嘅重點；先答客戶最想知嘅嘢，唔重播之前講過嘅內容，唔順手展開無關建議。
- no_match：一句答「我哋暫時冇符合你今次要求嘅計劃。」可以提一個關鍵條件令答覆更自然。答完就停，唔講資料未注明，唔推舊計劃、替代或者問客戶願唔願意改條件。
- availability：直接答有冇；有可以順帶講最相關嘅計劃名和客戶問嘅特點，唔寫完整推介。沿用語境暫查時可講清楚適用範圍，例如「如果繼續睇每月$150內」。
- lookup：只答客戶問到嘅已核實事實。如果「嗰款」有兩個合理指代，兩款答案一致就直接答；答案不同或未能一併核實，就只問客戶指邊一款。lookup_unavailable跟instruction簡短回覆，唔補推其他計劃。
- boundary：無關問題只需一句帶返到揀電訊計劃。consult只答有助選擇嘅部分，通常一兩句足夠。
- 真正影響選擇、明確對象或核對結果先問一句。聽得明就先答，條件足夠才推介；唔為推介而硬湊計劃。
""")
