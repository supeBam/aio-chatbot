"""AIO 工作流中的确定性业务规则。"""

import json
import re

from .model_output_schemas import RECOMMENDATION_LIMIT
from .plan_catalog import load_catalogs

TAG_CATALOGS, DETAILS = load_catalogs()


BUSINESSES = ("BN", "MS", "XE")
LEGACY_FIELDS = ("collected_tags", "uninterested_tags", "indifferent_tags")
ATTITUDES = ("interested", "uninterested", "indifferent")
PRIORITIES = ("required", "preferred", "unspecified")
NEEDS_DEFAULT = {
    'requirements': [],
    'next_requirement_id': 1,
    'last_validation_errors': [],
    'questions': [],
    'next_question_id': 1,
    'user_context': '',
    'pending_messages': [],
    'last_reply': '',
    'interview_pending': False,
    'ready_to_recommend': False,
    'last_recommended_codes': [],
    'interview_rounds': 0,
    'asked_topics': [],
    'last_interview_topic': '',
    'answer_history': [],
}
DEFAULTS = {
    "current_business": "",
    "bn_scope_codes": [],
    "routing_context": {"messages": [], "question": ""},
    **{f"{business.lower()}_{field}": value
       for business in BUSINESSES
       for field, value in (("plans_tags", []), ("catalog_status", "unloaded"), ("needs", NEEDS_DEFAULT))},
}


def dump(value):
    return json.dumps(value, ensure_ascii=False)

def clone(value):
    return json.loads(dump(value))

def parse_model_json(value):
    if not isinstance(value, str):
        raise ValueError("模型输出必须为JSON文本，请检查上游LLM节点的text")
    text = value.strip().lstrip("\ufeff").strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, flags=re.I | re.S)
        if not match:
            raise ValueError("模型输出围栏不完整或附带额外文本，必须仅返回一个JSON对象")
        text = match.group(1).strip()
    if not text:
        raise ValueError("模型输出为空，请检查上游LLM节点的text及结束原因")
    try:
        output = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("模型输出不是合法JSON（第" + str(error.lineno) + "行，第" + str(error.colno)
                         + "列），请检查上游LLM节点的text是否完整") from error
    if not isinstance(output, dict):
        raise ValueError("模型输出必须为单个JSON对象")
    return output

def strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)

def valid_code(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) is not None

OPERATOR_PATTERN = re.compile(
    r"中国移动(?:香港)?|中國移動(?:香港)?|中国联通|中國聯通|中国电信|中國電信|"
    r"3[ ]*香港|3[ ]*Hong[ ]*Kong|和记电讯|和記電訊|"
    r"香港宽频|香港寬頻|香港电讯|香港電訊|电讯盈科|電訊盈科|数码通|數碼通|"
    r"(?<![A-Za-z0-9])(?:China[ ]*Mobile|CMHK|HKBN|HKT|CSL|SmarTone|PCCW|Netvigator)(?![A-Za-z0-9])|"
    r"运营商|運營商|电讯商|電訊商",
    re.I
)

def neutral_product_name(text):
    return re.sub(r"HKBN[ ]+SAFE", "网络安全服务", text, flags=re.I)

def operator_reference(text):
    return OPERATOR_PATTERN.search(neutral_product_name(text)) is not None

def sales_tags(tags, neutral=False):
    return [neutral_product_name(tag) if neutral else tag for tag in tags if not operator_reference(tag)]

def sales_user_text(text):
    if not operator_reference(text):
        return neutral_product_name(text)
    clauses = re.split(r"[，,；;。！!？?\n]+", text)
    return "；".join(neutral_product_name(clause.strip()) for clause in clauses if clause.strip() and not operator_reference(clause))

def sales_document(text):
    text = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    lines = []
    for line in text.splitlines():
        line = neutral_product_name(line)
        had_operator = operator_reference(line)
        if had_operator and re.search(r"更快|最好|最强|最強|更稳|更穩|最佳|優於|优于", line):
            continue
        line = OPERATOR_PATTERN.sub("", line)
        if had_operator and re.fullmatch(
            r"\s*[-*]?\s*(?:(?:其他資訊|其他资讯|使用|所属|所屬|移动|流动|流動|網絡|网络|名稱|名称|"
            r"服務供應商|服务供应商|網絡供應商|网络供应商|[:：])[ ]*)+", line
        ):
            continue
        lines.append(re.sub(r"[ ]{2,}", " ", line).strip())
    return "\n".join(lines)


def sales_needs(needs):
    view = clone(needs)
    view.pop("active_selection", None)
    view.pop("selection_turn_requirements", None)
    view["requirements"] = [
        {**item, "text": neutral_product_name(item["text"]),
         "evidence": [sales_user_text(quote) for quote in item["evidence"]]}
        for item in view["requirements"] if not operator_reference(item["text"])
    ]
    view["questions"] = [
        {**item, "text": neutral_product_name(item["text"]),
         "evidence": [sales_user_text(quote) for quote in item["evidence"]]}
        for item in view.get("questions", []) if not operator_reference(item["text"])
    ]
    view["last_validation_errors"] = [sales_user_text(error) for error in view["last_validation_errors"]]
    for field in ("user_context", "last_reply"):
        view[field] = sales_user_text(view[field])
    view["pending_messages"] = [value for message in view["pending_messages"] if (value := sales_user_text(message))]
    for item in view["answer_history"]:
        item["question"] = sales_user_text(item["question"])
        item["answer"] = sales_user_text(item["answer"])
    return view

def sales_plans(plans):
    result = []
    for plan in plans:
        view = clone(plan)
        for field in ("tag", "matched_tags", "uninterested_matches", "unmatched_preferences"):
            if field in view:
                view[field] = sales_tags(view[field], neutral=True)
        if "markdown" in view:
            view["markdown"] = sales_document(view["markdown"])
            title = re.search(r"^(?:方案名称|方案名稱)[：:][ ]*(.+)$", view["markdown"], re.M)
            view["display_name"] = title.group(1).strip() if title else "服务套餐"
        result.append(view)
    return result

def validate_tags(plans):
    if not isinstance(plans, list):
        raise ValueError("套餐标签必须为对象数组")
    seen = set()
    for plan in plans:
        if not isinstance(plan, dict) or set(plan) != {"plan_code", "tag"}:
            raise ValueError("套餐标签对象结构无效")
        if not valid_code(plan["plan_code"]) or plan["plan_code"] in seen:
            raise ValueError("套餐编号无效或重复")
        seen.add(plan["plan_code"])
        if not strings(plan["tag"]) or len(set(plan["tag"])) != len(plan["tag"]):
            raise ValueError("套餐标签列表无效")
    return plans


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != set(DEFAULTS):
        raise ValueError("会话结构不兼容，请新建会话")
    if snapshot["current_business"] not in ("", *BUSINESSES):
        raise ValueError("当前业务无效")
    codes = snapshot["bn_scope_codes"]
    if not strings(codes) or any(not valid_code(code) for code in codes) or len(set(codes)) != len(codes):
        raise ValueError("BN可售范围无效")
    routing = snapshot["routing_context"]
    if not isinstance(routing, dict) or set(routing) != {"messages", "question"}:
        raise ValueError("业务澄清结构无效")
    if not strings(routing["messages"]) or len(routing["messages"]) > 20 or not isinstance(routing["question"], str):
        raise ValueError("业务澄清内容无效")
    for business in BUSINESSES:
        prefix = business.lower()
        validate_tags(snapshot[prefix + "_plans_tags"])
        if snapshot[prefix + "_catalog_status"] not in ("unloaded", "ready", "empty", "error"):
            raise ValueError("标签加载状态无效")
        needs = snapshot[prefix + "_needs"]
        if isinstance(needs, dict) and not ({"questions", "next_question_id"} & set(needs)):
            needs.update(questions=[], next_question_id=1)
        legacy_keys = (set(NEEDS_DEFAULT) - {"requirements", "next_requirement_id", "last_validation_errors"}) | set(LEGACY_FIELDS)
        if isinstance(needs, dict) and set(needs) == legacy_keys:
            migrated = []
            for field, attitude in zip(LEGACY_FIELDS, ATTITUDES):
                if not strings(needs[field]):
                    raise ValueError("旧偏好结构无效")
                for text in needs[field]:
                    if text.strip() and not operator_reference(text):
                        migrated.append({"id": "R" + str(len(migrated) + 1), "text": text,
                                         "attitude": attitude, "evidence": []})
            for field in LEGACY_FIELDS:
                needs.pop(field)
            needs.update(requirements=migrated, next_requirement_id=len(migrated) + 1, last_validation_errors=[])
        if not isinstance(needs, dict) or set(needs) - {"purchase_archive", "purchase_label", "active_selection", "selection_turn_requirements"} != set(NEEDS_DEFAULT):
            raise ValueError("用户需求结构无效或版本不兼容，请新建会话")
        archive = read_purchase_archive(needs)
        if not isinstance(needs.get("purchase_label", ""), str) or not isinstance(archive, dict):
            raise ValueError("选购记忆结构无效")
        for key, saved in archive.items():
            if (not re.fullmatch(r"S[1-9][0-9]*", key) or not isinstance(saved, dict)
                    or set(saved) != set(NEEDS_DEFAULT) | {"purchase_label"}
                    or not isinstance(saved["purchase_label"], str)):
                raise ValueError("已保存的选购结构无效")
        if not isinstance(needs["requirements"], list) or len(needs["requirements"]) > 100:
            raise ValueError("用户需求必须为最多100条的列表")
        seen = set()
        for item in needs["requirements"]:
            if not isinstance(item, dict) or set(item) not in (
                {"id", "text", "attitude", "evidence"}, {"id", "text", "attitude", "evidence", "priority"}
            ):
                raise ValueError("用户需求条目结构无效")
            if not isinstance(item["id"], str) or not re.fullmatch(r"R[1-9][0-9]*", item["id"]) or item["id"] in seen:
                raise ValueError("用户需求编号无效或重复")
            if not isinstance(item["text"], str) or not item["text"].strip() or item["attitude"] not in ATTITUDES or not strings(item["evidence"]):
                raise ValueError("用户需求内容无效")
            if item.get("priority", "unspecified") not in PRIORITIES:
                raise ValueError("用户需求优先级无效")
            seen.add(item["id"])
        if type(needs["next_requirement_id"]) is not int or needs["next_requirement_id"] < 1 or any(
            int(identifier[1:]) >= needs["next_requirement_id"] for identifier in seen
        ):
            raise ValueError("用户需求编号序列无效")
        if not strings(needs["last_validation_errors"]):
            raise ValueError("需求校验记录无效")
        if not isinstance(needs["questions"], list) or len(needs["questions"]) > 100:
            raise ValueError("用户咨询必须为最多100条的列表")
        question_ids = set()
        for item in needs["questions"]:
            if not isinstance(item, dict) or set(item) != {"id", "text", "evidence"}:
                raise ValueError("用户咨询结构无效")
            identifier = item["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"Q[1-9][0-9]*", identifier) or identifier in question_ids:
                raise ValueError("用户咨询编号无效或重复")
            if not isinstance(item["text"], str) or not item["text"].strip() or not strings(item["evidence"]):
                raise ValueError("用户咨询内容无效")
            question_ids.add(identifier)
        if type(needs["next_question_id"]) is not int or needs["next_question_id"] < 1 or any(
            int(identifier[1:]) >= needs["next_question_id"] for identifier in question_ids
        ):
            raise ValueError("用户咨询编号序列无效")
        for field in ("user_context", "last_reply"):
            if not isinstance(needs[field], str):
                raise ValueError("用户背景或上一轮回复无效")
        for field in ("interview_pending", "ready_to_recommend"):
            if type(needs[field]) is not bool:
                raise ValueError("访谈状态必须为布尔值")
        if not strings(needs["pending_messages"]) or len(needs["pending_messages"]) > 20:
            raise ValueError("待处理用户消息无效或过多")
        if not strings(needs["last_recommended_codes"]):
            raise ValueError("上次推荐编号无效")
        if type(needs["interview_rounds"]) is not int or not 0 <= needs["interview_rounds"] <= 4:
            raise ValueError("访谈轮数无效")
        if not strings(needs["asked_topics"]) or len(set(needs["asked_topics"])) != len(needs["asked_topics"]):
            raise ValueError("已问主题无效")
        topics = needs["asked_topics"] + [needs["last_interview_topic"]]
        if any(not isinstance(topic, str) or len(topic) > 500 for topic in topics) or any(not topic.strip() for topic in needs["asked_topics"]):
            raise ValueError("访谈主题必须为长度有限的文本")
        if not isinstance(needs["answer_history"], list) or len(needs["answer_history"]) > 20:
            raise ValueError("用户原话记录无效")
        for item in needs["answer_history"]:
            if not isinstance(item, dict) or set(item) != {"topic", "question", "answer"}:
                raise ValueError("用户原话记录结构无效")
            if not all(isinstance(item[field], str) for field in item) or not item["topic"].strip() or len(item["topic"]) > 500:
                raise ValueError("用户原话记录内容无效")
    return snapshot

def reply_bundle(snapshot, business, mode, instruction="", selected_plans=None, persist_reply=True, interview_topic=""):
    return {
        "snapshot": snapshot, "business": business, "mode": mode,
        "instruction": instruction, "selected_plans": selected_plans or [],
        "persist_reply": persist_reply, "interview_topic": interview_topic
    }

def read_purchase_archive(needs):
    archive = needs.get("purchase_archive", {})
    if isinstance(archive, str):
        archive = json.loads(archive)
    if not isinstance(archive, dict):
        raise ValueError("选购记忆结构无效")
    return archive


def read_active_selection(needs):
    selection = needs.get("active_selection")
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError("当前选购结构无效")
    if "turn_requirements" in selection:
        requirements = selection["turn_requirements"]
    else:
        requirements = needs["selection_turn_requirements"]
    if not isinstance(requirements, list):
        raise ValueError("当前选购临时需求结构无效")
    return {**selection, "turn_requirements": requirements}


def normalize_selection_state(snapshot):
    for business in BUSINESSES:
        needs = snapshot[business.lower() + "_needs"]
        if not isinstance(needs, dict):
            raise ValueError("用户需求结构无效")
        selection = needs.get("active_selection")
        if selection is not None:
            if not isinstance(selection, dict):
                raise ValueError("当前选购结构无效")
            if "turn_requirements" in selection:
                if "selection_turn_requirements" in needs:
                    raise ValueError("当前选购临时需求重复")
                needs["selection_turn_requirements"] = selection.pop("turn_requirements")
            if not isinstance(needs.get("selection_turn_requirements"), list):
                raise ValueError("当前选购临时需求结构无效")
        elif "selection_turn_requirements" in needs:
            raise ValueError("当前选购临时需求缺少选购状态")
        if "purchase_archive" in needs:
            needs["purchase_archive"] = dump(read_purchase_archive(needs))


def ingress(query, event_type, bn_plan_codes, **conversation):
    snapshot = clone(conversation)
    normalize_selection_state(snapshot)
    result = {
        "snapshot": snapshot, "ok": False, "event": "", "message": "", "error": "",
        "active_business": "", "last_reply": "", "routing_json": "{}"
    }
    try:
        validate_snapshot(snapshot)
        if event_type not in ("message", "address_add", "address_change"):
            raise ValueError("事件类型无效")
        if event_type == "message":
            if not isinstance(query, str) or not query.strip() or not isinstance(bn_plan_codes, str):
                raise ValueError("用户消息和套餐编号必须为字符串，用户消息不能为空")
            if bn_plan_codes:
                raise ValueError("普通消息不能携带地址结果")
            message = query.strip()
        else:
            if snapshot["current_business"] != "BN":
                snapshot["bn_needs"].pop("active_selection", None)
                snapshot["bn_needs"].pop("selection_turn_requirements", None)
            snapshot["current_business"] = "BN"
            if "active_selection" in snapshot["bn_needs"]:
                snapshot["bn_needs"]["active_selection"]["shown_plan_codes"] = []
            snapshot["bn_scope_codes"] = []
            snapshot["bn_plans_tags"] = []
            snapshot["bn_catalog_status"] = "error"
            archive = read_purchase_archive(snapshot["bn_needs"])
            for saved in archive.values():
                saved.update(last_reply="", last_recommended_codes=[], interview_pending=False, last_interview_topic="")
            if "purchase_archive" in snapshot["bn_needs"]:
                snapshot["bn_needs"]["purchase_archive"] = dump(archive)
            snapshot["bn_needs"].update(
                last_reply="", interview_pending=False, last_recommended_codes=[], last_interview_topic=""
            )
            if not isinstance(query, str) or not query.strip() or not isinstance(bn_plan_codes, str):
                raise ValueError("地址事件必须提供非空触发说明和字符串套餐编号")
            codes = []
            if bn_plan_codes.strip():
                for value in bn_plan_codes.split(","):
                    code = value.strip()
                    if not valid_code(code):
                        raise ValueError("套餐编号无效，请用英文逗号分隔，不能有空项")
                    if code not in codes:
                        codes.append(code)
            snapshot["bn_scope_codes"] = codes
            snapshot["bn_catalog_status"] = "unloaded" if codes else "empty"
            message = ""
        active = snapshot["current_business"]
        result.update(
            ok=True, event=event_type, message=message, active_business=active,
            last_reply=snapshot[active.lower() + "_needs"]["last_reply"] if active else "",
            routing_json=dump(snapshot["routing_context"])
        )
    except (ValueError, TypeError) as error:
        result["error"] = "入口校验失败：" + str(error)
    return result


def input_failure(snapshot, error):
    return {"bundle": reply_bundle(snapshot, "", "error", error, persist_reply=False)}


def address_business():
    return {"text": '{"business":"BN","mentioned_businesses":["BN"],"clarification":"","intent":"shopping","business_requests":[]}'}


def prepare(llm_output, snapshot, event, message):
    original = clone(snapshot)
    snapshot = clone(snapshot)
    result = {"snapshot": original, "ok": False, "business": "", "intent": "shopping", "text": "", "mode": "error"}
    try:
        route = parse_model_json(llm_output)
        if not isinstance(route, dict) or not {"business", "mentioned_businesses", "clarification", "intent"} <= set(route) or set(route) - {"business", "mentioned_businesses", "clarification", "intent", "business_requests"}:
            raise ValueError("业务识别结构无效")
        business, mentioned, question, intent = (
            route["business"], route["mentioned_businesses"], route["clarification"], route["intent"]
        )
        if business not in ("", *BUSINESSES) or intent not in ("consult", "shopping", "boundary") or not isinstance(question, str):
            raise ValueError("业务识别内容无效")
        if not strings(mentioned) or any(b not in BUSINESSES for b in mentioned) or len(set(mentioned)) != len(mentioned):
            raise ValueError("涉及业务列表无效")
        requests = route.get("business_requests", [])
        if not isinstance(requests, list) or len(requests) > len(BUSINESSES):
            raise ValueError("独立业务请求必须为最多三个业务的列表")
        if event != "message" and (business != "BN" or intent != "shopping" or question):
            raise ValueError("地址事件必须确定进入BN")
        if intent == "boundary":
            if business or question or requests:
                raise ValueError("范围外回复不能携带选购业务、请求或澄清")
            result.update(mode="boundary", text="簡短帶回電訊計劃選購，不處理無關問題。")
            return result
        if question.strip() or not business:
            pending = snapshot["routing_context"]["messages"]
            if len(pending) >= 20:
                raise ValueError("待澄清消息过多")
            if message:
                pending.append(message)
            question = question.strip() or "您想了解宽带、移动通讯，还是影音等订阅服务？"
            snapshot["routing_context"]["question"] = question
            result.update(snapshot=snapshot, text=question, mode="clarify")
            return result
        snapshot["current_business"] = business
        messages = snapshot["routing_context"]["messages"] + ([message] if message else [])
        dispatch = {}
        assigned_fragments = set()
        for request in requests:
            if not isinstance(request, dict) or set(request) != {"business", "messages"}:
                raise ValueError("独立业务请求结构无效")
            target, fragments = request["business"], request["messages"]
            if not isinstance(target, str) or target not in BUSINESSES or target in dispatch or target not in mentioned + [business]:
                raise ValueError("独立业务请求的业务无效、重复或未声明")
            if not strings(fragments) or not fragments or len(fragments) != len(set(fragments)) or any(not value.strip() for value in fragments):
                raise ValueError("独立业务请求必须包含非空且不重复的用户原话片段")
            if any(not any(fragment in source for source in messages) for fragment in fragments):
                raise ValueError("业务片段不是本轮或待澄清消息中的原话")
            if assigned_fragments.intersection(fragments):
                raise ValueError("同一原话片段不能广播到多个业务，请分别确认独立购买目标")
            assigned_fragments.update(fragments)
            dispatch[target] = fragments
        if dispatch and (business not in dispatch or intent != "shopping"):
            raise ValueError("独立业务请求必须包含当前购买业务，不能用于一般咨询")
        if intent == "shopping":
            if not dispatch:
                dispatch = {business: messages}
            for target, fragments in dispatch.items():
                needs = snapshot[target.lower() + "_needs"]
                if len(needs["pending_messages"]) + len(fragments) > 20:
                    raise ValueError("待处理用户消息过多")
                needs["pending_messages"].extend(fragments)
        snapshot["routing_context"] = {"messages": [], "question": ""}
        result.update(snapshot=snapshot, ok=True, business=business, intent=intent)
    except (ValueError, TypeError) as error:
        result["text"] = "业务识别校验失败：" + str(error)
        pending = original["routing_context"]["messages"]
        if event == "message" and message and message not in pending and len(pending) < 20:
            pending.append(message)
    return result


def business_validation(llm_output, snapshot, event, message):
    if not isinstance(llm_output, dict):
        return {"retry": True, "error": "结构化输出必须为JSON对象", "text": ""}
    llm_output = dump(llm_output)
    result = prepare(llm_output, snapshot, event, message)
    return {"retry": not result["ok"] and result["mode"] == "error", "error": result["text"], "text": llm_output}


def scope_reply(snapshot, text, mode):
    return {"bundle": reply_bundle(snapshot, "", mode, text, persist_reply=False)}


def mock_fetch_tags(business, plan_codes):
    source = TAG_CATALOGS[business]
    if business != "BN":
        return source
    by_code = {plan["plan_code"]: plan for plan in source}
    if any(code not in by_code for code in plan_codes):
        raise ValueError("后端未找到部分套餐编号，不能扩大范围或忽略编号")
    return [by_code[code] for code in plan_codes]


def fetch_tags(business, snapshot):
    codes = snapshot["bn_scope_codes"] if business == "BN" else []
    request = dump({"business": business, "plan_codes": codes})
    try:
        plans = validate_tags(mock_fetch_tags(business, codes))
        if business == "BN" and {p["plan_code"] for p in plans} != set(codes):
            raise ValueError("标签结果缺少或增加了地址套餐")
        bundle = {"status": "ready" if plans else "empty", "error": "", "plans_tags": plans, "request_json": request}
    except (ValueError, TypeError) as error:
        bundle = {"status": "error", "error": "加载套餐标签失败：" + str(error), "plans_tags": [], "request_json": request}
    return bundle


def catalog_context(snapshot, business, intent):
    prefix = business.lower()
    plans = snapshot[prefix + "_plans_tags"]
    loaded = snapshot[prefix + "_catalog_status"]
    needs_load = False
    status, error = "ready", ""
    if intent == "consult":
        status = "consult"
    elif business == "BN" and not snapshot["bn_scope_codes"]:
        status = "empty" if loaded == "empty" else "error" if loaded == "error" else "missing"
        if status == "error":
            error = "当前地址结果无效，请重新提交地址；这不代表无覆盖。"
    elif plans:
        if loaded != "ready" or business == "BN" and {p["plan_code"] for p in plans} != set(snapshot["bn_scope_codes"]):
            status, error = "error", "标签数组与当前可售范围不一致，请重新加载。"
    elif loaded == "empty":
        status = "empty"
    else:
        needs_load = True
    return needs_load, {"status": status, "error": error, "plans_tags": plans, "request_json": ""}


def shortlist_tags(tags):
    other_tags = []
    services = {}
    for tag in sales_tags(tags):
        service, separator, detail = tag.partition("；")
        if service.startswith("服务: ") and separator:
            services.setdefault(service, []).append(detail)
        else:
            other_tags.append(tag)
    return other_tags + ["；".join([service, *dict.fromkeys(details)]) for service, details in services.items()]


def catalog_view(bundle, snapshot, business):
    snapshot = clone(snapshot)
    prefix = business.lower()
    status, plans = bundle["status"], validate_tags(bundle["plans_tags"])
    if bundle["request_json"]:
        snapshot[prefix + "_catalog_status"] = status
        snapshot[prefix + "_plans_tags"] = plans
    if status == "error":
        snapshot[prefix + "_plans_tags"] = []
        snapshot[prefix + "_catalog_status"] = "error"
        snapshot[prefix + "_needs"]["last_recommended_codes"] = []
    mode, instruction = {
        "ready": ("process", ""),
        "missing": ("request_address", "家居宽频需要查询地址覆盖范围，请填写地址表单。"),
        "empty": ("no_plans", "本次查询成功，但没有可推荐的套餐。可以更换地址或咨询其他业务。"),
        "error": ("error", bundle["error"]),
        "consult": ("consult", "")
    }[status]
    active = snapshot[prefix + "_needs"]
    view = {key: value for key, value in active.items()
            if key not in ("purchase_archive", "active_selection", "selection_turn_requirements")}
    selection = read_active_selection(active)
    if selection is not None:
        view["active_selection"] = {"turn_requirements": selection["turn_requirements"]}
    view["purchase_archive"] = {
        key: {"label": saved["purchase_label"], "requirements": saved["requirements"]}
        for key, saved in read_purchase_archive(active).items()
    }
    return {
        "snapshot": snapshot, "process": mode == "process",
        "business": business, "mode": mode, "instruction": instruction,
        "plans_tags": plans, "plans_tags_json": json.dumps([
            {"plan_code": plan["plan_code"], "tag": shortlist_tags(plan["tag"])} for plan in plans
        ], ensure_ascii=False, separators=(",", ":")),
        "needs_json": dump(view)
    }


def catalog(snapshot, business, intent):
    needs_load, context = catalog_context(snapshot, business, intent)
    if needs_load:
        context = fetch_tags(business, snapshot)
    result = catalog_view(context, snapshot, business)
    result["loaded"] = needs_load
    return result


def pre_reply(snapshot, business, mode, instruction):
    return {"bundle": reply_bundle(snapshot, business, mode, instruction)}


def withdraws_optional_service(evidence):
    return any(re.fullmatch(
        r"(?!.*(?:必須|必须|包含|包括|連帶|连带|必带|必帶|[的嘅](?:套餐|[計计][劃划])))"
        r"(?:[不唔](?:想)?加[配購购].+|(?:不要|不需要|唔要|唔需要).+[了喇])",
        quote.strip(" ，,。！？!?"),
    ) for quote in evidence)


def merge(llm_output, snapshot, business, event="message"):
    snapshot = clone(snapshot)
    needs = snapshot[business.lower() + "_needs"]
    needs["requirements"] = [item for item in needs["requirements"] if not operator_reference(item["text"])]
    needs["questions"] = [item for item in needs["questions"] if not operator_reference(item["text"])]
    needs["last_validation_errors"] = []
    pending = needs["pending_messages"]
    saved_selection = read_active_selection(needs)
    if business == "BN" and event == "address_change" and not pending and saved_selection:
        needs.update(questions=[], next_question_id=1)
        bundle = reply_bundle(snapshot, business, "analyze")
        bundle.update(clone(saved_selection), delivery_goal="selection")
        return {"bundle": bundle}
    try:
        output = parse_model_json(llm_output)
        if not isinstance(output, dict) or "changes" not in output or set(output) - {"changes", "question_changes", "recommend", "clarification", "reason", "readiness", "context_update", "turn_intent", "availability_scope", "turn_conditions", "temporarily_excluded_ids", "purchase_switch", "link_request", "catalog_recommendation", "delivery_goal", "selection_context", "replaced_temporary_ids"}:
            raise ValueError("需求输出结构无效，必须包含changes且不能有未知字段")
        for field, default in (("clarification", ""), ("reason", ""), ("context_update", None)):
            output.setdefault(field, default)
        if not isinstance(output["changes"], list) or len(output["changes"]) > 50:
            raise ValueError("需求变更必须为最多50条的列表")
        output.setdefault("question_changes", [])
        if not isinstance(output["question_changes"], list) or len(output["question_changes"]) > 50:
            raise ValueError("咨询变更必须为最多50条的列表")
        if not isinstance(output["clarification"], str) or not isinstance(output["reason"], str):
            raise ValueError("澄清与依据必须为文本")
        def verify_evidence(quotes):
            if not strings(quotes) or not quotes or any(not quote.strip() for quote in quotes):
                raise ValueError("修改需求必须提供非空用户原话证据")
            if any(not any(quote in message for message in pending) for quote in quotes):
                raise ValueError("证据不是待处理用户消息的原话")
        link_request = output.get("link_request", "")
        if not isinstance(link_request, str) or (link_request and not link_request.strip()):
            raise ValueError("办理链接请求必须为原话或空文本")
        if link_request and (not pending or link_request not in pending[-1]):
            link_request = ""
        turn_intent = output.get("turn_intent", "shop")
        catalog_recommendation = output.get("catalog_recommendation", "")
        if not isinstance(catalog_recommendation, str):
            raise ValueError("目录推荐请求必须为原话或空文本")
        if (turn_intent != "availability" or not catalog_recommendation.strip()
                or not any(catalog_recommendation in message for message in pending)):
            catalog_recommendation = ""
        conditions = output.get("turn_conditions", [])
        excluded_ids = output.get("temporarily_excluded_ids", [])
        availability_scope = output.get("availability_scope", "catalog")
        selection_context = output.get("selection_context", "continue" if turn_intent == "shop" and saved_selection else "new")
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
        no_contract_preference = re.compile(
            r"(?:不考[虑慮]|唔考[虑慮])合[约約]期|合[约約]期(?:[无無]所[谓謂]|冇所謂|冇要求|不限|不[限拘])"
        )
        for item in conditions:
            if (not isinstance(item, dict) or set(item) not in (
                    {"text", "evidence"}, {"text", "evidence", "priority"})
                    or not isinstance(item["text"], str) or not item["text"].strip()
                    or item.get("priority", "required") not in ("required", "preferred")
                    or operator_reference(item["text"])):
                raise ValueError("临时查询条件无效")
            verify_evidence(item["evidence"])
            if all(no_contract_preference.fullmatch(quote.strip(" ，,。！？!?"))
                   for quote in item["evidence"]):
                continue
            priority = item.get("priority", "required")
            text, evidence = item["text"], item["evidence"]
            preference = [quote for quote in evidence
                          if re.match(r"^(最好|優先|优先|如果可以|如果得)", quote.strip())]
            business_only = r"流[动動]通[讯訊](?:[计計][划劃]|plan)?"
            if preference and (len(preference) == len(evidence) or (
                    business == "MS" and all(
                        quote in preference or re.fullmatch(business_only, quote.strip(), re.I)
                        for quote in evidence))):
                priority = "preferred"
                if len(preference) != len(evidence):
                    text, evidence = " ".join(preference), preference
            turn_requirements.append({"id": "T" + str(len(turn_requirements) + 1), "text": text,
                                      "evidence": evidence, "attitude": "interested",
                                      "priority": priority})
        if other_options and same_purchase:
            turn_requirements = []
        if turn_intent == "lookup":
            if output["changes"] or output["context_update"] is not None:
                raise ValueError("查询指定计划不得更新选购条件")
            if not output["question_changes"] and not output["clarification"].strip():
                raise ValueError("查询指定计划必须提供本轮问题或待澄清问题")
        if any(not isinstance(item, dict) or item.get("operation") != "add"
               for item in output["question_changes"]) and "turn_intent" in output:
            raise ValueError("本轮问题只使用新增操作")
        can_continue = same_purchase and (prior_temporary or needs["requirements"] or needs["last_recommended_codes"])
        if (turn_intent == "availability" and not turn_requirements and not can_continue
                and not output["clarification"].strip()):
            raise ValueError("查找某类计划必须提供本轮条件或待澄清问题")
        if switch is not None:
            if not isinstance(switch, dict) or set(switch) != {"target", "evidence"}:
                raise ValueError("选购切换结构无效")
            verify_evidence(switch["evidence"])
            archive = read_purchase_archive(needs)
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
            current.update(purchase_archive=dump(archive), pending_messages=pending)
            snapshot[business.lower() + "_needs"] = needs = current
        if not set(excluded_ids).issubset({item["id"] for item in needs["requirements"]}):
            raise ValueError("临时排除只能引用当前选购的已保存需求")
        if "turn_intent" in output:
            needs.update(questions=[], next_question_id=1)
        errors = []
        for field, list_key, id_key, counter, prefix, label in (
            ("changes", "requirements", "requirement_id", "next_requirement_id", "R", "需求"),
            ("question_changes", "questions", "question_id", "next_question_id", "Q", "咨询"),
        ):
            changes = output[field]
            targets = [change.get(id_key) for change in changes if isinstance(change, dict)]
            additions = [change.get("text") for change in changes
                         if isinstance(change, dict) and change.get("operation") == "add"]
            fields = {"business", "operation", id_key, "text", "evidence"}
            if field == "changes":
                fields.add("attitude")
            for index, change in enumerate(changes):
                try:
                    allowed = (fields, fields | {"priority"}) if field == "changes" else (fields,)
                    if not isinstance(change, dict) or set(change) not in allowed:
                        raise ValueError("变更条目结构无效")
                    if change["business"] != business:
                        raise ValueError("需求变更不属于当前业务，禁止跨业务写入")
                    operation, identifier = change["operation"], change[id_key]
                    if operation not in ("add", "update", "remove") or not isinstance(identifier, str):
                        raise ValueError("变更操作或需求编号无效")
                    verify_evidence(change["evidence"])
                    if not isinstance(change["text"], str):
                        raise ValueError("需求文本无效")
                    if field == "changes" and (change["attitude"] not in ATTITUDES
                                              or change.get("priority", "unspecified") not in PRIORITIES):
                        raise ValueError("需求态度或优先级无效")
                    if operator_reference(change["text"]):
                        raise ValueError("运营商偏好不属于套餐需求")
                    values = {"text": change["text"].strip(), "evidence": change["evidence"]}
                    if field == "changes":
                        values["attitude"] = change["attitude"]
                        if "priority" in change:
                            values["priority"] = change["priority"]
                    existing = next((item for item in needs[list_key] if item["id"] == identifier), None)
                    if (field == "changes" and operation == "update" and existing
                            and existing.get("attitude") == "interested" and change["attitude"] == "uninterested"
                            and withdraws_optional_service(change["evidence"])):
                        operation = "remove"
                    if operation == "add":
                        if identifier or not values["text"] or additions.count(change["text"]) > 1:
                            raise ValueError("新增需求必须使用空编号和唯一的非空文本")
                        same = next((item for item in needs[list_key] if item["text"] == values["text"]), None)
                        if same:
                            if field == "changes" and (
                                same["attitude"] != values["attitude"]
                                or same.get("priority", "unspecified") != values.get("priority", "unspecified")
                            ):
                                raise ValueError("改变态度或优先级必须按已有需求编号update")
                            continue
                        if len(needs[list_key]) >= 100:
                            raise ValueError("已达到100条需求或咨询上限")
                        identifier = prefix + str(needs[counter])
                        needs[list_key].append({"id": identifier, **values})
                        needs[counter] += 1
                    else:
                        if not existing or targets.count(identifier) != 1:
                            raise ValueError("更新或撤回必须引用唯一的已有需求编号")
                        if operation == "remove":
                            needs[list_key].remove(existing)
                        elif values["text"]:
                            existing.update(values)
                        else:
                            raise ValueError("更新需求文本不能为空")
                except (ValueError, TypeError) as error:
                    errors.append(label + "变更" + str(index + 1) + "：" + str(error))
        context = output["context_update"]
        if context is not None:
            try:
                if not isinstance(context, dict) or set(context) != {"value", "evidence"} or not isinstance(context["value"], str):
                    raise ValueError("用户背景更新结构无效")
                verify_evidence(context["evidence"])
                needs["user_context"] = sales_user_text(context["value"].strip())
            except (ValueError, TypeError) as error:
                errors.append("背景更新：" + str(error))
        answered_interview = turn_intent == "shop" and bool(pending) and needs["interview_pending"] and bool(needs["last_interview_topic"])
        if pending:
            topic = needs["last_interview_topic"] if answered_interview else "refinement" if needs["last_recommended_codes"] else "initial"
            question = needs["last_reply"] if answered_interview else ""
            records = [
                {"topic": topic, "question": question, "answer": message} for message in pending
            ]
            if needs["answer_history"][-len(records):] != records:
                needs["answer_history"] = (needs["answer_history"] + records)[-20:]
        if answered_interview:
            needs["interview_rounds"] = min(4, needs["interview_rounds"] + 1)
        if turn_intent == "shop":
            needs["interview_pending"] = False
        if errors:
            needs.update(ready_to_recommend=False, last_validation_errors=errors)
            return {"bundle": reply_bundle(snapshot, business, "error", "需求校验失败，明确部分已保存：" + "；".join(errors))}
        needs["requirements"] = [
            item for item in needs["requirements"]
            if not (item["attitude"] == "uninterested" and withdraws_optional_service(item["evidence"]))
        ]
        needs["pending_messages"] = []
        needs["ready_to_recommend"] = False
        bundle = reply_bundle(snapshot, business, "analyze", output["clarification"].strip())
        if turn_intent == "availability" and availability_scope == "catalog":
            excluded_ids = [item["id"] for item in needs["requirements"]]
        delivery_goal = output.get("delivery_goal")
        if delivery_goal is None:
            delivery_goal = ("link" if turn_intent == "lookup" and link_request else
                             "selection" if turn_intent == "shop" or catalog_recommendation else "facts")
        if selection_context == "alternatives" and same_purchase:
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
        if delivery_goal not in ("selection", "facts", "link"):
            raise ValueError("交付目标无效")
        if (delivery_goal == "selection" and turn_intent == "lookup"
                or delivery_goal == "link" and (turn_intent != "lookup" or not link_request)
                or delivery_goal != "link" and link_request):
            raise ValueError("交付目标与查询对象或客户原话不一致")
        bundle.update(delivery_goal=delivery_goal, turn_intent=turn_intent, availability_scope=availability_scope,
                      turn_requirements=turn_requirements, temporarily_excluded_ids=excluded_ids, link_request=link_request, catalog_recommendation=catalog_recommendation)
        return {"bundle": bundle}
    except (ValueError, TypeError) as error:
        needs.update(ready_to_recommend=False, last_validation_errors=[str(error)])
        return {"bundle": reply_bundle(snapshot, business, "error", "需求校验失败：" + str(error))}


def extract_validation(llm_output, snapshot, business, event="message"):
    if not isinstance(llm_output, dict):
        return {"retry": True, "error": "结构化输出必须为JSON对象", "text": ""}
    llm_output = dump(llm_output)
    result = merge(llm_output, snapshot, business, event)
    return {"retry": result["bundle"]["mode"] == "error", "error": result["bundle"]["instruction"], "text": llm_output}


def decision_state(bundle):
    snapshot = bundle["snapshot"]
    prefix = bundle["business"].lower()
    needs = sales_needs(snapshot[prefix + "_needs"])
    needs.pop("purchase_archive", None)
    needs["turn_intent"] = bundle.get("turn_intent", "shop")
    needs["link_requested"] = bool(bundle.get("link_request"))
    needs["excluded_plan_codes"] = bundle.get("excluded_plan_codes", [])
    if needs["turn_intent"] == "lookup":
        needs["requirements"] = []
    excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
    needs["requirements"] = [item for item in needs["requirements"] if item["id"] not in excluded_ids]
    needs["requirements"].extend(bundle.get("turn_requirements", []))
    by_code = {plan["plan_code"]: plan for plan in snapshot[prefix + "_plans_tags"]}
    previous_codes = needs["last_recommended_codes"]
    comparison = {
        "latest_user_message": needs["answer_history"][-1]["answer"] if needs["answer_history"] else "",
        "previous_recommendations": [
            {"plan_code": code, "tag": sales_tags(by_code[code]["tag"], neutral=True)}
            for code in previous_codes if code in by_code
        ],
        "unavailable_previous_codes": [code for code in previous_codes if code not in by_code],
    }
    return {
        "snapshot": snapshot, "mode": bundle["mode"],
        "instruction": bundle["instruction"],
        "needs_json": dump(needs), "comparison_context_json": dump(comparison)
    }


def product_decision(bundle, plans_tags, llm_output):
    bundle = clone(bundle)
    snapshot, business = bundle["snapshot"], bundle["business"]
    needs = snapshot[business.lower() + "_needs"]
    needs["ready_to_recommend"] = False
    try:
        output = parse_model_json(llm_output)
        if "follow_up" not in output and output.get("action") in ("recommend", "boundary"):
            output["follow_up"] = None
        if not isinstance(output, dict) or set(output) != {"action", "selected_plan_codes", "follow_up", "reason"}:
            raise ValueError("产品决策结构无效")
        action, selected, follow_up = output["action"], output["selected_plan_codes"], output["follow_up"]
        if action not in ("interview", "clarify", "recommend", "boundary"):
            raise ValueError("产品决策动作无效")
        if not isinstance(output["reason"], str) or not output["reason"].strip():
            raise ValueError("产品决策缺少依据")
        available = {plan["plan_code"] for plan in validate_tags(plans_tags)}
        if not strings(selected) or len(selected) > 5 or len(set(selected)) != len(selected):
            raise ValueError("候选必须为最多五个不重复的编号")
        invalid_codes = [code for code in selected if code not in available]
        if invalid_codes:
            raise ValueError(
                "候选编号不在当前可售范围；无效编号=" + dump(invalid_codes)
                + "；当前允许编号=" + dump([plan["plan_code"] for plan in plans_tags])
                + "；请从当前目录逐字复制完整编号，不得缩写、截断或自行补全"
            )
        if set(selected).intersection(bundle.get("excluded_plan_codes", [])):
            raise ValueError("本轮要看其他套餐，候选不能重复上次展示的选项")
        if business == "BN" and set(available) != set(snapshot["bn_scope_codes"]):
            raise ValueError("决策目录与地址范围不一致")
        can_check = bool(bundle.get("turn_requirements")) or (
            bundle.get("turn_intent") == "lookup" and bool(selected)
        )
        if bundle["instruction"] and action != "clarify" and (
            bundle.get("link_request") or not (action == "recommend" and can_check)
        ):
            raise ValueError("尚有明确需求歧义，不能跳过澄清")
        if action in ("interview", "clarify"):
            fields = {"topic", "information_needed", "selection_impact", "blocking"}
            if not isinstance(follow_up, dict) or set(follow_up) != fields:
                raise ValueError("追问必须说明待确认信息和对产品选择的影响")
            for field in ("topic", "information_needed", "selection_impact"):
                if not isinstance(follow_up[field], str) or not follow_up[field].strip() or len(follow_up[field]) > 500:
                    raise ValueError("追问内容无效")
            if type(follow_up["blocking"]) is not bool or action == "clarify" and not follow_up["blocking"]:
                raise ValueError("澄清必须声明真实阻塞")
            if follow_up["topic"] in needs["asked_topics"] and not bundle["instruction"]:
                raise ValueError("不能重复已问主题；真实需求改口冲突须由提取阶段明确说明")
        elif follow_up is not None:
            raise ValueError("非追问动作不能携带追问")
        turn_intent = bundle.get("turn_intent", "shop")
        if action == "interview" and turn_intent in ("lookup", "availability"):
            raise ValueError("查询应直接核对；只有对象或结果范围歧义才能澄清")
        ordinal = re.search(r"第\s*([12一二兩两])\s*[款個个]", bundle.get("link_request", ""))
        if turn_intent == "lookup" and action == "recommend" and ordinal:
            index = 0 if ordinal.group(1) in ("1", "一") else 1
            previous = needs["last_recommended_codes"]
            expected = previous[index:index + 1]
            if expected and expected[0] in available and selected != expected:
                raise ValueError("办理入口必须使用上次展示顺序中的指定套餐")
            if not expected or expected[0] not in available:
                selected = []
        if turn_intent == "lookup" and len(selected) > RECOMMENDATION_LIMIT:
            raise ValueError("指定计划查询最多核对两个可能对象")
        if action == "recommend":
            if turn_intent == "shop" and not (needs["requirements"] or needs["questions"] or needs["user_context"] or needs["interview_rounds"] or needs["answer_history"]):
                raise ValueError("尚无明确需求或有效访谈回答，不能擅自推荐")
            if selected:
                needs["ready_to_recommend"] = True
                mode = "recommend" if turn_intent == "shop" else turn_intent
                bundle.update(mode=mode, instruction="", interview_topic="")
            else:
                mode = "lookup_unavailable" if turn_intent == "lookup" else "no_match"
                instruction = "而家未能核實你問嗰款計劃。" if mode == "lookup_unavailable" else ""
                bundle.update(mode=mode, instruction=instruction, selected_plans=[], interview_topic="")
        elif action == "boundary":
            bundle.update(mode="boundary", instruction="简短说明仅能依据当前可售套餐资料提供新购比较；不能承诺资料外优惠、绕过资格或披露内部信息。自然回到产品选择，不索取无关个人资料。", interview_topic="")
        else:
            bundle.update(mode=action, instruction=dump(follow_up), interview_topic=follow_up["topic"])
        return {"bundle": bundle, "shortlist_json": dump({"selected_plan_codes": selected, "reason": output["reason"]})}
    except (ValueError, TypeError) as error:
        needs["last_validation_errors"] = [str(error)]
        bundle.update(mode="error", instruction="产品决策校验失败，请重试：" + str(error), selected_plans=[])
        return {"bundle": bundle, "shortlist_json": dump({"selected_plan_codes": [], "reason": "决策校验失败"})}


def shortlist_validation(bundle, plans_tags, llm_output):
    if not isinstance(llm_output, dict):
        return {"retry": True, "error": "结构化输出必须为JSON对象", "text": ""}
    follow_up = llm_output.get("follow_up")
    if isinstance(follow_up, dict) and follow_up.get("blocking") in ("true", "false"):
        llm_output = {**llm_output, "follow_up": {**follow_up, "blocking": follow_up["blocking"] == "true"}}
    llm_output = dump(llm_output)
    result = product_decision(bundle, plans_tags, llm_output)
    return {"retry": result["bundle"]["mode"] == "error", "error": result["bundle"]["instruction"], "text": llm_output}


def plan_facts(business, markdown):
    lines = markdown.splitlines()
    facts = {}
    renewal = [line.removeprefix("- ") for line in lines
               if re.match(r"^- 合[约約]期[：:]", line) and re.search(r"自[动動][续續][约約]", line)]
    admin_fee = [line.removeprefix("- ") for line in lines
                 if line.startswith("- ") and re.search(r"行政[费費]", line) and "$" in line]
    if renewal or admin_fee:
        amounts = {amount for line in admin_fee for amount in re.findall(r"\$\s*(\d+(?:\.\d+)?)", line)}
        facts["booking_terms"] = {"renewal": renewal, "admin_fee": admin_fee,
                                  "admin_fee_amounts_differ": len(amounts) > 1}
    if business == "BN":
        name = re.search(r"^方案名[称稱][：:][ \t]*([^\n]+)", markdown, re.M)
        name = name.group(1) if name else ""
        router = r"(?:路由器|\brouter\b)"
        if re.search(r"(?:需|須|必須|必须)\s*(?:加購|加购|另購|另购).*" + router, name, re.I):
            offer = "required_add_on"
        elif re.search(r"(?:可選購|可选购|可加購|可加购).*" + router, name, re.I):
            offer = "optional"
        elif re.search(r"(?:連|连|附送|包括|包含).*" + router, name, re.I):
            offer = "included"
        else:
            offer = "unconfirmed"
        facts["router_offer"] = offer
    if business == "MS":
        speeds = set()
        for line in lines:
            if re.match(r"^\s*-?\s*(?:流量[后後][续續]安排[：:]|其[后後]|超[過过]首)", line):
                speeds.update(float(value) for value in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*Mbps(?![A-Za-z])", line, re.I))
        if speeds:
            facts["after_data_limit"] = {
                "speeds_mbps": [format(speed, "g") for speed in sorted(speeds)],
                "conflict": len(speeds) > 1,
            }
    return facts


def plan_monthly_fee(markdown):
    section = re.search(r"^###[ ]+基础信息[ ]*$\n(.*?)(?=^#{1,6}[ ]|\Z)", markdown, re.M | re.S)
    if section:
        price = re.search(r"^套餐参考月费[：:][ ]*([0-9]+(?:\.[0-9]+)?)[ ]*$", section.group(1), re.M)
        if price:
            return price.group(1)
    return None

def mock_rank_and_details(business, plans_tags, selected_plan_codes):
    plans_by_code = {plan["plan_code"]: plan for plan in validate_tags(plans_tags)}
    candidates = []
    for code in selected_plan_codes:
        if code not in DETAILS[business]:
            raise ValueError("后端未找到候选套餐详情")
        markdown = DETAILS[business][code]
        candidates.append({**plans_by_code[code], "markdown": markdown,
                           "plan_monthly_fee": plan_monthly_fee(markdown),
                           **plan_facts(business, sales_document(markdown))})
    return candidates


def rank(bundle, plans_tags, llm_output):
    snapshot, business = bundle["snapshot"], bundle["business"]
    prefix = business.lower()
    needs = snapshot[prefix + "_needs"]
    current = sales_needs(needs)
    excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
    requirements = [] if bundle["mode"] == "lookup" else [
        item for item in current["requirements"] if item["id"] not in excluded_ids
    ] + bundle.get("turn_requirements", [])
    request = {
        "business": business, "plan_codes": [plan["plan_code"] for plan in plans_tags],
        "requirements": requirements,
        "questions": current["questions"], "limit": len(plans_tags)
    }
    try:
        if not needs["ready_to_recommend"] or snapshot[prefix + "_catalog_status"] != "ready" or not plans_tags:
            raise ValueError("需求或候选尚未就绪，禁止获取推荐详情")
        if business == "BN" and set(request["plan_codes"]) != set(snapshot["bn_scope_codes"]):
            raise ValueError("推荐候选与地址范围不一致")
        output = json.loads(llm_output)
        if not isinstance(output, dict) or set(output) != {"selected_plan_codes", "reason"}:
            raise ValueError("初筛输出结构无效")
        selected = output["selected_plan_codes"]
        if not strings(selected) or len(selected) > 5 or len(set(selected)) != len(selected):
            raise ValueError("初筛编号必须为最多五个不重复的字符串")
        if set(selected).intersection(bundle.get("excluded_plan_codes", [])):
            raise ValueError("本轮详情不能读取已排除的套餐")
        if any(code not in request["plan_codes"] for code in selected):
            raise ValueError("初筛编号不在当前可售范围")
        if not isinstance(output["reason"], str):
            raise ValueError("初筛依据必须为字符串")
        request.update(selected_plan_codes=selected, shortlist_reason=output["reason"])
        candidates = mock_rank_and_details(business, plans_tags, selected)
        if not isinstance(candidates, list) or len(candidates) != len(selected):
            raise ValueError("后端结果必须覆盖所有请求的计划")
        allowed, seen = set(selected), set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("plan_code"), str):
                raise ValueError("后端候选编号无效")
            code = candidate["plan_code"]
            if code not in allowed or code in seen:
                raise ValueError("后端候选越界或重复")
            seen.add(code)
            if not isinstance(candidate.get("markdown"), str) or not candidate["markdown"].strip():
                raise ValueError("候选缺少完整套餐原文")
            if not strings(candidate.get("tag")):
                raise ValueError("候选标签结构无效")
        if [candidate["plan_code"] for candidate in candidates] != selected:
            raise ValueError("候选详情与初筛编号或顺序不一致")
        return {
            "candidates_json": dump(candidates), "sales_candidates_json": dump(sales_plans(candidates)),
            "request_json": dump(request), "error": ""
        }
    except (ValueError, TypeError) as error:
        return {
            "candidates_json": "[]", "sales_candidates_json": "[]",
            "request_json": dump(request), "error": "获取推荐详情失败：" + str(error)
        }


def source_quote(quote, markdown):
    if quote in markdown:
        return quote
    fragments = quote.splitlines()
    if len(fragments) < 2 or any(not fragment.strip() for fragment in fragments) or len(set(fragments)) != len(fragments):
        raise ValueError("核对引用不属于对应套餐原文")
    lines = markdown.splitlines(keepends=True)
    content = [line.rstrip("\r\n") for line in lines]
    positions = []
    for fragment in fragments:
        matches = [index for index, line in enumerate(content)
                   if line == fragment or (line.startswith("- ") and line[2:] == fragment)]
        if len(matches) != 1:
            raise ValueError("核对引用不属于对应套餐原文")
        positions.append(matches[0])
    if len(set(positions)) != len(positions):
        raise ValueError("核对引用不属于对应套餐原文")
    first, last = min(positions), max(positions)
    if any(not line.strip() or line.lstrip().startswith("#") for line in content[first:last + 1]):
        raise ValueError("核对引用不属于对应套餐原文")
    return "".join(lines[first:last + 1]).rstrip("\r\n")

def checked_link_label(label, markdown):
    if not isinstance(label, str):
        return ""
    label = label.strip()
    if not re.fullmatch(r"查看 [\w +＋·（）()-]{1,21}", label) or operator_reference(label):
        return ""
    name = label.removeprefix("查看 ").removesuffix("方案").removesuffix("計劃").strip()
    name = re.sub(r"\s+", "", name).casefold()
    source = re.sub(r"\s+", "", markdown).casefold()
    return label if name and name in source else ""


def matches_monthly_limits(plan, requirements):
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


def validate_selection(llm_output, candidates_json, needs_json):
    result = {"recommendations_json": "[]", "error": ""}
    try:
        output = parse_model_json(llm_output)
        candidates = json.loads(candidates_json)
        needs = json.loads(needs_json)
        if not isinstance(output, dict) or set(output) != {"assessments", "reason"}:
            raise ValueError("精选结果必须包含逐需求核对和依据")
        if not isinstance(candidates, list) or not isinstance(needs, dict) or not isinstance(needs.get("requirements"), list):
            raise ValueError("候选或用户需求结构无效")
        by_code = {}
        for plan in candidates:
            if not isinstance(plan, dict) or not valid_code(plan.get("plan_code")) or not isinstance(plan.get("markdown"), str):
                raise ValueError("候选原文无效")
            if plan["plan_code"] in by_code:
                raise ValueError("候选编号重复")
            by_code[plan["plan_code"]] = plan
        if not isinstance(output["assessments"], list) or any(not isinstance(item, dict) or not isinstance(item.get("plan_code"), str) for item in output["assessments"]):
            raise ValueError("套餐核对列表或编号无效")
        selected = [item["plan_code"] for item in output["assessments"]]
        if not strings(selected) or len(selected) > RECOMMENDATION_LIMIT or len(set(selected)) != len(selected) or any(code not in by_code for code in selected):
            raise ValueError("精选编号必须为候选内最多2个唯一编号")
        if not isinstance(output["reason"], str) or not isinstance(output["assessments"], list):
            raise ValueError("精选依据或核对列表无效")
        requirements = {item["id"] for item in needs["requirements"]}
        required_ids = {item["id"] for item in needs["requirements"]
                        if item.get("priority") == "required"}
        router_inclusion_requests = {
            item["id"] for item in needs["requirements"]
            if item.get("attitude", "interested") == "interested"
            and re.match(r"^(?:套餐|[計计][劃划])?\s*(?:需要|要求|要)?\s*(?:包含|包括|包埋|連|连)\s*(?:路由器|router\b)", item.get("text", ""), re.I)
        }
        eligible = []
        questions = {item["id"] for item in needs.get("questions", [])}
        assessed = {}
        answered = {}
        labels = {}
        for assessment in output["assessments"]:
            if (not isinstance(assessment, dict) or not {"plan_code", "requirements"} <= set(assessment)
                    or set(assessment) - {"plan_code", "requirements", "info_answers", "reason", "link_label"}):
                raise ValueError("套餐核对结构无效")
            if "reason" in assessment and not isinstance(assessment["reason"], str):
                raise ValueError("套餐核对的可选reason必须为文本")
            code = assessment["plan_code"]
            if not isinstance(code, str) or code not in selected or code in assessed or not isinstance(assessment["requirements"], list):
                raise ValueError("核对套餐编号越界或重复")
            seen = set()
            for item in assessment["requirements"]:
                if not isinstance(item, dict) or set(item) != {"requirement_id", "status", "quote", "explanation"}:
                    raise ValueError("逐需求核对结构无效")
                identifier = item["requirement_id"]
                if not isinstance(identifier, str) or identifier not in requirements:
                    raise ValueError("套餐" + code + "核对需求编号越界：" + dump(identifier)
                                     + "；有效需求编号=" + dump(sorted(requirements)))
                if identifier in seen:
                    raise ValueError("套餐" + code + "核对需求编号重复：" + identifier + "；每个需求只能评估一次")
                if item["status"] not in ("supported", "unsupported", "unknown"):
                    raise ValueError("核对状态只能为supported、unsupported或unknown")
                quote = item["quote"]
                if not isinstance(quote, str) or not isinstance(item["explanation"], str) or not item["explanation"].strip():
                    raise ValueError("核对引用和解释无效")
                if item["status"] != "unknown" and not quote.strip():
                    raise ValueError("明确支持或不支持必须引用原文")
                if quote:
                    item["quote"] = source_quote(quote, by_code[code]["markdown"])
                seen.add(identifier)
            if seen != requirements:
                raise ValueError("套餐" + code + "缺少需求编号=" + dump(sorted(requirements - seen))
                                 + "；精选套餐必须覆盖每条当前有效需求，缺失信息标记unknown")
            offer = by_code[code].get("router_offer")
            if offer in ("unconfirmed", "optional", "required_add_on"):
                for item in assessment["requirements"]:
                    if item["requirement_id"] in router_inclusion_requests and item["status"] == "supported":
                        item.update(status="unknown", quote="", explanation="原文未确认路由器随该套餐提供。")
            assessed[code] = assessment["requirements"]
            info_answers = assessment.get("info_answers", [])
            if not isinstance(info_answers, list):
                raise ValueError("套餐咨询回答必须为列表")
            seen_questions = set()
            for item in info_answers:
                if not isinstance(item, dict) or set(item) != {"question_id", "status", "quote", "answer"}:
                    raise ValueError("套餐咨询回答结构无效")
                identifier = item["question_id"]
                if not isinstance(identifier, str) or identifier not in questions:
                    raise ValueError("套餐" + code + "咨询编号越界：" + dump(identifier)
                                     + "；有效咨询编号=" + dump(sorted(questions)))
                if identifier in seen_questions:
                    raise ValueError("套餐" + code + "咨询编号重复：" + identifier)
                if item["status"] not in ("answered", "unknown"):
                    raise ValueError("咨询状态只能为answered或unknown")
                if not isinstance(item["answer"], str) or not item["answer"].strip() or not isinstance(item["quote"], str):
                    raise ValueError("咨询回答或引用无效")
                if item["status"] == "answered" and not item["quote"].strip():
                    raise ValueError("明确咨询结论必须引用原文")
                if item["quote"]:
                    item["quote"] = source_quote(item["quote"], by_code[code]["markdown"])
                seen_questions.add(identifier)
            if seen_questions != questions:
                raise ValueError("套餐" + code + "缺少咨询编号=" + dump(sorted(questions - seen_questions))
                                 + "；资料未注明时回答unknown，不省略咨询")
            answered[code] = info_answers
            labels[code] = checked_link_label(assessment.get("link_label"), by_code[code]["markdown"])
            if (matches_monthly_limits(by_code[code], needs["requirements"])
                    and all(item["status"] == "supported" for item in assessed[code]
                            if item["requirement_id"] in required_ids)):
                eligible.append(code)
        if set(assessed) != set(selected):
            raise ValueError("每款精选套餐都必须有核对结果")
        if needs.get("turn_intent") == "lookup" and set(eligible) != set(by_code):
            raise ValueError("查询必须核对全部指定对象，不能任意漏掉其中一款")
        result["recommendations_json"] = dump([
            {**by_code[code], "requirement_assessments": assessed[code], "info_answers": answered[code], "link_label": labels[code]} for code in eligible
        ])
    except (ValueError, TypeError, KeyError) as error:
        result["error"] = "精选核对失败：" + str(error)
    return result


def select_validation(llm_output, candidates_json, needs_json):
    if not isinstance(llm_output, dict):
        return {"retry": True, "error": "结构化输出必须为JSON对象", "text": ""}
    llm_output = dump(llm_output)
    result = validate_selection(llm_output, candidates_json, needs_json)
    return {"retry": bool(result["error"]), "error": result["error"], "text": llm_output}


def recommendation(bundle, recommendations_json, selection_error, rank_error, candidates_json="[]"):
    bundle = clone(bundle)
    if rank_error or selection_error:
        bundle.update(mode="error", instruction=rank_error or selection_error, selected_plans=[])
    else:
        plans = json.loads(recommendations_json)
        bundle["selected_plans"] = plans
        mode = bundle["mode"]
        if mode == "lookup" and not plans:
            bundle.update(mode="lookup_unavailable", instruction="而家未能核實你問嗰款計劃。")
        elif mode in ("recommend", "availability") and not plans:
            bundle.update(mode="no_match", instruction="")
            needs = bundle["snapshot"][bundle["business"].lower() + "_needs"]
            if needs["questions"]:
                bundle["consultation_plans"] = [
                    {"plan_code": plan["plan_code"], "markdown": plan["markdown"]}
                    for plan in json.loads(candidates_json)
                ]
    return {"bundle": bundle}


def build_link_products(plans):
    products = []
    seen = set()
    for plan in plans:
        code = plan.get("plan_code")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", code) or code in seen:
            continue
        seen.add(code)
        products.append({"plan_code": code, "link_label": plan.get("link_label") or ""})
        if len(products) == RECOMMENDATION_LIMIT:
            break
    labels = [re.sub(r"\s+", "", product["link_label"]).casefold() for product in products]
    for index, product in enumerate(products):
        if not labels[index] or labels.count(labels[index]) > 1:
            product["link_label"] = "查看計劃" if len(products) == 1 else f"查看第 {index + 1} 款"
    return products


def present_plans(bundle):
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
                or code in bundle.get("excluded_plan_codes", [])
                or business == "BN" and code not in bundle["snapshot"]["bn_scope_codes"]):
            continue
        seen.add(code)
        plans.append(plan)
        if len(plans) == RECOMMENDATION_LIMIT:
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


def reply_context(bundle, message):
    snapshot = clone(bundle["snapshot"])
    presentation, plans, plan_summary, products = present_plans(bundle)
    mode = presentation["mode"]
    if bundle["business"] in BUSINESSES and presentation["goal"] == "selection" and mode in (
            "recommend", "availability", "no_match"):
        snapshot[bundle["business"].lower() + "_needs"]["active_selection"] = {
            "shown_plan_codes": list(dict.fromkeys(
                bundle.get("excluded_plan_codes", []) + presentation["plan_codes"]
            )),
            "turn_intent": bundle.get("turn_intent", "shop"),
            "availability_scope": bundle.get("availability_scope", "catalog"),
            "temporarily_excluded_ids": bundle.get("temporarily_excluded_ids", []),
        }
        snapshot[bundle["business"].lower() + "_needs"]["selection_turn_requirements"] = bundle.get("turn_requirements", [])
    business = bundle["business"]
    needs = sales_needs(snapshot[business.lower() + "_needs"]) if business else clone(NEEDS_DEFAULT)
    needs.pop("purchase_archive", None)
    if mode in ("availability", "no_match", "lookup_unavailable"):
        excluded_ids = set(bundle.get("temporarily_excluded_ids", []))
        saved = needs["requirements"]
        current = [item for item in saved if item["id"] not in excluded_ids]
        needs = {"requirements": current + bundle.get("turn_requirements", []),
                 "questions": needs["questions"]}
        if mode == "no_match" and needs["questions"]:
            needs["consultation_plans"] = bundle.get("consultation_plans", [])

        if mode == "availability" and bundle.get("availability_scope") == "catalog":
            needs["previous_purchase_requirements"] = saved
    if mode == "error":
        needs["last_reply"] = ""
        needs["last_recommended_codes"] = []
    instruction = sales_user_text(bundle["instruction"]) if operator_reference(bundle["instruction"]) else bundle["instruction"]
    if operator_reference(message):
        instruction += " 用户涉及运营商话题；不复述名称、不作评价或比较，只依据给定套餐的流量、费用、合约和服务内容提供销售建议。"
    if presentation["goal"] == "link" and mode == "lookup":
        if products:
            instruction += "\n客戶想要指定計劃嘅辦理入口，請自然回應。入口由前端展示。"
        else:
            instruction += "\n如果有多款可能指代，只問客戶想要邊款。"
    if plan_summary:
        instruction += (
            "\n按推薦版式承接「今次揀咗」同有序套餐名單，名單含名稱及 {plan_code}。"
            "直接按同一順序用「第 1 款」「第 2 款」講已核實嘅計劃內容、參考月費、合約同重要取捨。"
            "毋須重列名單，亦唔將精選數目講成全目錄可售總數。"
        )
    return {
        "snapshot": snapshot, "business": business, "mode": presentation["mode"],
        "instruction": instruction, "needs_json": dump(needs),
        "selected_plans_json": dump(plans),
        "user_message": sales_user_text(message),
        "link_products": products,
        "presentation": presentation,
        "plan_summary": plan_summary,
        "persist_reply": bundle["persist_reply"],
        "interview_topic": bundle["interview_topic"],
        "plans_tags_json": "[]"
    }


def address_action():
    return {"action": "request_address"}


def product_link_action(products, service_type):
    return {
        "frontend_action": "SHOW_PRODUCT_LINK" if products else "NONE",
        "service_type": service_type,
        "products": products,
    }


def record_reply(snapshot, business, mode, text, selected_plans_json, persist_reply, interview_topic, presentation=None, plan_summary=""):
    snapshot = clone(snapshot)
    if not business and mode == "clarify":
        snapshot["routing_context"]["question"] = text
    if persist_reply and business and mode != "boundary":
        needs = snapshot[business.lower() + "_needs"]
        needs.update(questions=[], next_question_id=1)
        if presentation and presentation["goal"] == "selection" and presentation["plan_codes"]:
            needs["last_recommended_codes"] = presentation["plan_codes"]
        elif presentation is None and mode == "availability":
            shown_codes = [
                plan["plan_code"] for plan in json.loads(selected_plans_json)
                if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(plan["plan_code"]) + r"(?![A-Za-z0-9_-])", text)
            ]
            if shown_codes:
                needs["last_recommended_codes"] = shown_codes
        if needs["interview_pending"] and mode in ("lookup", "lookup_unavailable", "availability", "no_match"):
            return {"snapshot": snapshot}
        needs["last_reply"] = plan_summary + text
        needs["interview_pending"] = mode in ("interview", "clarify")
        needs["last_interview_topic"] = interview_topic if mode in ("interview", "clarify") else ""
        if mode == "interview" and interview_topic and interview_topic not in needs["asked_topics"]:
            needs["asked_topics"].append(interview_topic)
        if mode == "recommend" and presentation is None:
            needs["last_recommended_codes"] = [p["plan_code"] for p in json.loads(selected_plans_json)]
        elif mode in ("request_address", "no_plans"):
            needs["last_recommended_codes"] = []
    return {"snapshot": snapshot}


def model_failure(error_type, error_message):
    return {"retry": True, "error": "模型节点失败（" + str(error_type) + "）：" + str(error_message), "text": ""}


def pack_bundle(bundle):
    """将会话字段展开一层，避免分支输出再嵌套整个快照。"""
    snapshot = bundle["snapshot"]
    context = {key: value for key, value in bundle.items() if key not in ("snapshot", "mode")}
    return {**snapshot, "mode": bundle["mode"], "context_json": dump(context)}


def unpack_bundle(bundle):
    snapshot = {name: bundle[name] for name in DEFAULTS}
    return {**json.loads(bundle["context_json"]), "snapshot": snapshot, "mode": bundle["mode"]}


def forward_bundle(bundle):
    return {"bundle": bundle}
