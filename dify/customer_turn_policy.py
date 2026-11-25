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
- consult只用于简短回答能帮助选购的一般概念；现售计划详情和能否找到某类计划交shopping。家居宽频选购中问有没有家居电话计划，按BN查询可售宽频组合。纯感谢可用boundary，不展开或重推。
- 家居宽频对话里，“我想要固话套餐”“想睇家居電話計劃”等新选购目标也走BN shopping，核对当前地址的宽频连家居电话组合。只有客户明说只要独立固话、不要宽频时，才按独立固话目标处理。
- 宽频对话里，D+、Disney、Disney+ 都指 Disney+；“一期等级”“一起登记”“加多个”等结合上下文理解为宽频组合选购。若刚查过该组合，再问登记有无优惠，仍交 BN shopping 核对原组合。
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
- 上一轮已展示同一宽频及订阅组合，客户重述想一起登记并问优惠、费用或条款时，优先视为lookup。把优惠写入question_changes；仅问“有冇優惠”不代表必须额外减价，不新增优惠硬条件。除非客户明确更改组合或明确说没有优惠就不考虑。
- turn_intent=availability：问有无某类型计划。把当轮要核对的条件放到turn_conditions，每项附原话。疑问句里的新条件不默认写入长期记忆。用户在同一句明确确认了其他独立条件时仍可更新。
- availability_scope默认为catalog：只按本轮问的范围查当前可售目录，即使话题仍是同一业务，也不套用旧预算、速度或其他已保存条件。只有明确引用原范围，例如“照頭先啲條件，有冇包埋家居電話嘅？”或“如果淨係放寬到$200，有冇？”，才用current_purchase。“頭先嗰款包唔包”是lookup；“我要加埋家居電話”是shop。
- 已推荐之后，若用户只说“我想要某类计划”或“推荐某类计划”，且没有说明要加进原来的选择，这是查询新选项，用availability/catalog。陈述句也适用。只有表达继续原选择、要把某项加进去或修改原条件时才合并；“固话、固定电话、家居电话”视为同一服务。
- temporarily_excluded_ids默认为[]，仅在current_purchase下排除本轮范围以外的旧条件。只点名沿用旧预算时，保留预算，排除未被引用的旧网速等条件；“其他照舊”保留未被调整的条件。例如“其他照舊，如果預算加到$200有冇？”暂时替换预算，不改保存的条件。
- 模棱两可时，按最近相关选购语境暂查，只有不同理解会实质改变答案且不能先作有用回应时，才用clarification注明唯一需要问的区别。暂时推断不写入changes或context_update。若上轮在澄清，结合原先问题理解这次短答案。
- purchase_switch默认为null。同一业务中明确另开选购时用{"target":"new","evidence":["用户原话"]}；重回已保存的选购时target为purchase_archive里的编号。按当前客户语句识别服务对象，单纯加一项条件不切换。新选购只新增条件；回到已保存选购时以该对象的需求编号更新。
- 本轮问题只用question_changes中的add，处理完便不再视为当前问题。只有明示的购买条件写入changes。只问计划详情时可照样回答，即使该计划不合适。
- 输出必须带turn_intent、availability_scope、turn_conditions、temporarily_excluded_ids、purchase_switch。临时查询范围仅用于availability；没有消息的地址事件沿用当前选购，五个字段分别为shop、catalog、[]、[]、null。

例：刚推荐过每月$130内的1000M家居宽频，用户说“我想要固话套餐”。查当前地址可售的宽频连家居电话组合；旧条件保留给之后的原选购。
{"changes":[],"question_changes":[],"clarification":"","reason":"本轮想睇家居电话计划，未说加进之前的选择","context_update":null,"turn_intent":"availability","availability_scope":"catalog","turn_conditions":[{"text":"家居宽频包含家居电话","evidence":["我想要固话套餐"]}],"temporarily_excluded_ids":[],"purchase_switch":null}

例：上轮刚展示两款上台计划；用户说“頭先嗰款包唔包日本漫遊？”。交下游定位可能指代的计划和核实，不新增购买条件。
{"changes":[],"question_changes":[{"business":"MS","operation":"add","question_id":"","text":"用户指的上一轮计划是否包括日本漫游","evidence":["頭先嗰款包唔包日本漫遊？"]}],"clarification":"","reason":"核对上一轮计划详情","context_update":null,"turn_intent":"lookup","availability_scope":"catalog","turn_conditions":[],"temporarily_excluded_ids":[],"purchase_switch":null}

例：上一轮已展示可选 Disney+ 的宽频，用户说“我想加多个D+一期等级，有没有优惠？”。只核对刚展示组合的优惠。
{"changes":[],"question_changes":[{"business":"BN","operation":"add","question_id":"","text":"上一轮宽频连 Disney+ 组合同步登记有无优惠","evidence":["我想加多个D+一期等级，有没有优惠？"]}],"clarification":"","reason":"沿用刚展示的组合核对优惠","context_update":null,"turn_intent":"lookup","availability_scope":"catalog","turn_conditions":[],"temporarily_excluded_ids":[],"purchase_switch":null}
""")
    item = prompt("extract")
    lines = item["text"].splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('{"changes"') or '|' in line:
            continue
        example = json.loads(line)
        if "turn_intent" in example:
            continue
        example.update(turn_intent="shop", availability_scope="catalog", turn_conditions=[],
                       temporarily_excluded_ids=[], purchase_switch=None)
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
         '"reason", "readiness", "context_update", "turn_intent", "availability_scope", "turn_conditions", "temporarily_excluded_ids", "purchase_switch"}:'),
        ('        errors = []\n',
         '''        turn_intent = output.get("turn_intent", "shop")
        conditions = output.get("turn_conditions", [])
        excluded_ids = output.get("temporarily_excluded_ids", [])
        availability_scope = output.get("availability_scope", "catalog")
        if turn_intent not in ("shop", "lookup", "availability"):
            raise ValueError("本轮意图无效")
        if not isinstance(conditions, list) or len(conditions) > 20 or (conditions and turn_intent != "availability"):
            raise ValueError("本轮查找条件无效")
        if not strings(excluded_ids) or (excluded_ids and (not conditions or turn_intent != "availability")):
            raise ValueError("本轮临时排除条件无效")
        if availability_scope not in ("catalog", "current_purchase") or (
                availability_scope == "current_purchase" and turn_intent != "availability"):
            raise ValueError("本轮目录查询范围无效")
        if excluded_ids and availability_scope != "current_purchase":
            raise ValueError("只有沿用原选购时才需要逐项临时排除旧条件")
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
        if turn_intent == "availability" and availability_scope == "catalog":
            excluded_ids = [item["id"] for item in needs["requirements"]]
        bundle.update(turn_intent=turn_intent, availability_scope=availability_scope,
                      turn_requirements=turn_requirements, temporarily_excluded_ids=excluded_ids)
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
        "- 初筛以避免遗漏为目标", "- 最多3次可选访谈", "- selected_plan_codes在追问时也保留",
    )
    item["text"] = "\n".join(line for line in item["text"].splitlines() if not line.startswith(obsolete))
    item["text"] = replace(
        item["text"],
        '{"action":"recommend","selected_plan_codes":["DEMO_BN_1000"],"follow_up":null,"reason":"本轮可售目录没有比上轮1000M更快的候选，仅保留未改善备选，必须披露带宽没有提升"}',
        '{"action":"recommend","selected_plan_codes":[],"follow_up":null,"reason":"唯一标签显示与上次同速，没有值得核对的候选"}',
    )
    item["text"] = replace(
        item["text"],
        '用户未声明拒绝比较超预算方案。',
        '用户已确认80元为预算上限。',
    )
    item["text"] = replace(
        item["text"],
        '{"action":"recommend","selected_plan_codes":["DEMO_BN_2000"],"follow_up":null,"reason":"2000M相对上轮1000M有提升，但378元超出80元预算；保留作有缺口的比较，不能因98元较便宜就只返回1000M并称其更快"}',
        '{"action":"recommend","selected_plan_codes":[],"follow_up":null,"reason":"两个标签均与明确的预算上限冲突，初筛为空"}',
    )
    add_policy("shortlist", """
## 本轮回复动作
- shop按当前选购条件，availability按本轮查询范围，从全部可售标签中挑0至5款。结合本轮原话、有效条件、上下文及每款tag判断相关性，按核对价值排序；标签与硬条件明确冲突就排除。标签有具体相关线索但条款待核实时可以入选。只选值得读原文的计划，有几款选几款，不凑数，无合理候选返回[]。
- lookup只定位客户所指的当前可售计划，明确对象选一款，两个合理指代最多选两款。确需客户辨别时用clarify；所指计划已不可售或无可核对对象时用recommend和空列表。禁止顺带挑其他计划。
- 三种意图都只读取selected_plan_codes内的原文。空列表结束查询；原文不支持本轮条件时也不自动补查其他计划。
- 相关问答尽量先答重点。追问每轮只问一个会改变选择的问题，不因轮数到了就推不符合的计划。
- 用户要求的规格、必须服务和比较方向都须核实；未知或没有改善的旧计划不能作为推荐兜底。客人允许看替代时按他明确放宽的条件重新核对，其他限制继续有效。
- 历史答问用于理解指代和避免重问，不能借历史补回已撤回条件，不从其他选购读取条件。
- 整套计划月费及预算初筛只看「套餐参考月费」标签。「优惠月费」属于相应的单项服务，不代替或加总成整套月费。
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
        if turn_intent == "lookup" and len(selected) > 2:
            raise ValueError("指定计划查询最多核对两个可能对象")
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
        ('            needs["ready_to_recommend"] = True\n            bundle.update(mode="recommend", instruction="当轮展示已核对的初步比较；披露预算、条件及未知信息的缺口，不等待额外确认。", interview_topic="")',
         '''            if selected:
                needs["ready_to_recommend"] = True
                mode = "recommend" if turn_intent == "shop" else turn_intent
                bundle.update(mode=mode, instruction="", interview_topic="")
            else:
                mode = "lookup_unavailable" if turn_intent == "lookup" else "no_match"
                instruction = "而家未能核實你問嗰款計劃。" if mode == "lookup_unavailable" else ""
                bundle.update(mode=mode, instruction=instruction, selected_plans=[], interview_topic="")'''),
    )

    route = nodes["recommend_route"]["cases"][0]
    route["logical_operator"] = "or"
    for mode in ("lookup", "availability"):
        route["conditions"].append({**route["conditions"][0], "id": "details-" + mode, "value": mode})
    edit_code(
        "rank",
        ('if not isinstance(candidates, list) or len(candidates) > 5:',
         'if not isinstance(candidates, list) or len(candidates) != len(selected):'),
        ('后端候选必须为最多五款套餐的列表', '后端结果必须覆盖所有请求的计划'),
        ('    request = {\n        "business": business, "plan_codes":',
         '''    current = sales_needs(needs)
    excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
    requirements = [] if bundle["mode"] == "lookup" else [
        item for item in current["requirements"] if item["id"] not in excluded_ids
    ] + bundle.get("turn_requirements", [])
    request = {
        "business": business, "plan_codes":'''),
        ('"requirements": sales_needs(needs)["requirements"],\n        "questions": sales_needs(needs)["questions"]',
         '"requirements": requirements,\n        "questions": current["questions"]'),
        ('''        if bundle["mode"] != "lookup":
            prioritized = set(selected)
            selected += [code for code in request["plan_codes"] if code not in prioritized]
        request.update(selected_plan_codes=selected, shortlist_reason=output["reason"])''',
         '''        request.update(selected_plan_codes=selected, shortlist_reason=output["reason"])'''),
        ('def mock_rank_and_details(business, plans_tags, selected_plan_codes):',
         '''def plan_monthly_fee(markdown):
    section = re.search(r"^###[ ]+基础信息[ ]*$\\n(.*?)(?=^#{1,6}[ ]|\\Z)", markdown, re.M | re.S)
    if section:
        price = re.search(r"^套餐参考月费[：:][ ]*([0-9]+(?:\\.[0-9]+)?)[ ]*$", section.group(1), re.M)
        if price:
            return price.group(1)
    return None

def mock_rank_and_details(business, plans_tags, selected_plan_codes):'''),
        ('        candidates.append({**plans_by_code[code], "markdown": DETAILS[business][code]})',
         '''        markdown = DETAILS[business][code]
        candidates.append({**plans_by_code[code], "markdown": markdown,
                           "plan_monthly_fee": plan_monthly_fee(markdown)})'''),
    )
    nodes["rank"]["title"] = "僅讀取本輪初篩計劃原文"

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
    item["text"] = item["text"].replace("月费：180元", "套餐参考月费: 180")
    add_policy("select", """
## 核对与本轮结果
- 只核对本轮初筛提供的原文。从这些计划中按当前问题和有效条件精选0至2款，只有一款合适就只选一款，无可核实的匹配返回空数组，流程到此结束。
- shop和availability只返回所有priority=required条件均有原文证据支持的计划。临时T编号与R编号同样需要核对。找不到就返回空assessments。preferred用来排序，不默许放宽其他条件。
- lookup只核对用户所指计划的问题，缺资料的答案标unknown。若有两个合理指代，必须一起核对才能答，不能只取其中一个；无可核对结果时返回空数组。不要用其他计划替换被问对象。
- 用户想要 Disney+ 时，原文明确可选 Disney+ 的二选一组合可以满足选择该服务的需求。说明需要选 Disney+、适用档位和期数；若用户明确要求两项同时包含，按原文核对。核对优惠时，组合已包含或可选的权益与额外减价分别判断。
- 预算上限、计划月费和价格比较一律用plan_monthly_fee核对，即当前原文「基础信息」中的「套餐参考月费」。有结论时引用该字段原文；缺少整套月费则标记unknown。不使用单项服务「优惠月费」回答月费问题，也不重新加总或扣减回赠。客户询问单项服务月费时，不把整套月费当作该服务月费；只提供整套套餐参考月费，并说明不提供单项服务月费。
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
         '''        if needs.get("turn_intent") == "lookup" and eligible and set(eligible) != set(by_code):
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
            bundle.update(mode="no_match", instruction="")'''),
    )
    edit_code(
        "reply_context",
        ('    if bundle["mode"] == "error":',
         '''    needs.pop("purchase_archive", None)
    if bundle["mode"] in ("availability", "no_match", "lookup_unavailable"):
        excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
        saved = needs["requirements"]
        current = [item for item in saved if item["id"] not in excluded_ids]
        needs = {"requirements": current + bundle.get("turn_requirements", [])}
        if bundle["mode"] == "availability" and bundle.get("availability_scope") == "catalog":
            needs["previous_purchase_requirements"] = saved
    if bundle["mode"] == "error":'''),
        ('    if bundle["mode"] == "recommend":\n        instruction += (',
         '''    if bundle["mode"] == "recommend":
        two_plans = len(bundle["selected_plans"]) == 2
        heading_number = "序號. " if two_plans else ""
        if two_plans:
            instruction += "\\n兩款預設簡短分段。客戶要求用表格，或者至少有兩項會影響選擇嘅差異可以簡短並排比較，先考慮用表格。"
        instruction += ('''),
        ('"\\n逐款格式：\\n##### 1. {display_name}（{plan_code}）"',
         '"\\n分段格式：\\n##### " + heading_number + "{display_name}（{plan_code}）"'),
        ('"\\nBN、MS、XE统一分段展示，只有一款也必须保持版式，不能合并成大段文字。"',
         '"\\n只用一種版式。分段時，每款計劃單獨成節。"'),
    )
    edit_code(
        "record_reply",
        ('    if persist_reply and business:', '    if persist_reply and business and mode != "boundary":'),
        ('        needs["last_reply"] = text',
         '''        needs.update(questions=[], next_question_id=1)
        if mode == "availability":
            shown_codes = [
                plan["plan_code"] for plan in json.loads(selected_plans_json)
                if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(plan["plan_code"]) + r"(?![A-Za-z0-9_-])", text)
            ]
            if shown_codes:
                needs["last_recommended_codes"] = shown_codes
        if needs["interview_pending"] and mode in ("lookup", "lookup_unavailable", "availability", "no_match"):
            return {"snapshot": snapshot}
        needs["last_reply"] = text'''),
    )

    item = prompt("reply")
    item["text"] = replace(
        item["text"], "- BN、MS、XE使用统一清晰结构，只有一款也不得写成一整段。",
        "- 推薦兩款時預設簡短分段。如果客戶要求用表格，或者有至少兩項會影響佢選擇嘅差異適合簡短並排，就可以用一個 Markdown 表格。"
        "第一欄列比較項目，其餘兩欄按推薦順序列計劃名稱同編號，只列對客戶有用嘅已核實重點。"
        "差異主要得一項，先用一句講清楚，再簡短列兩款。重要條件如果放入表格會難讀，就用分段。表格同分段揀一種，唔重列。",
    )
    item["text"] = replace(
        item["text"], "每款以Markdown五级标题“##### 序号. display_name（plan_code）”单独成节，",
        "分段時，每款用 Markdown 五級標題。只有一款時用「##### display_name（plan_code）」，標題前省略序號。"
        "兩款分段時用「##### 1. display_name（plan_code）」和「##### 2. display_name（plan_code）」。每款",
    )
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
- 所有月費回答只列整套「計劃參考月費」，一律用plan_monthly_fee（原文「基礎信息」嘅「套餐參考月費」）。缺少呢個值時省略金額。客戶問單項服務月費時，說明只提供整套計劃參考月費，唔將整套月費當成單項服務月費，亦唔列單項「優惠月費」。必要另收費用照常列明，唔自行加總月費或再扣回贈。
- no_match：用一句自然嘅香港粵語講今次暫時搵唔到符合條件嘅計劃，按客戶今輪問嘅規格、組合、預算或地址範圍組句。只交代今次結果，唔斷言全目錄冇貨或地址只支援某個上限。答完就停，唔推舊計劃或替代，亦唔追問。
- availability：直接答有冇；有可以順帶講相關計劃嘅規格同整套參考月費，唔寫完整推介。提到某款時附上原有計劃編號，方便下一輪接住查。previous_purchase_requirements只可以用嚟提示與上一輪嘅明顯分別，例如今輪搵到嘅計劃超出舊預算，唔可以用佢判斷今輪有冇。客戶明確要求沿用原條件時，回覆講清楚查詢範圍。
- 問登記優惠時，先交代已核實可揀嘅服務同整套參考月費。額外優惠未能核實，就只講未能確認有額外優惠，唔反過來否定已核實嘅計劃或權益。二選一要講清楚係可選其中一項。
- lookup：只答客戶問到嘅已核實事實。如果「嗰款」有兩個合理指代，兩款答案一致就直接答；答案不同或未能一併核實，就只問客戶指邊一款。lookup_unavailable跟instruction簡短回覆，唔補推其他計劃。
- boundary：無關問題只需一句帶返到揀電訊計劃。consult只答有助選擇嘅部分，通常一兩句足夠。
- 真正影響選擇、明確對象或核對結果先問一句。聽得明就先答，條件足夠才推介；唔為推介而硬湊計劃。
""")
