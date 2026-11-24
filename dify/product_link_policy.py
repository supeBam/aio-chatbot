"""将已核实的套餐转换为前端办理链接动作。"""

import copy
import json

if __package__:
    from .customer_turn_policy import replace
    from .model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
else:
    from customer_turn_policy import replace
    from model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT


PRODUCT_LINK_CODE = r'''import json
import re


def main(mode, link_requested, selected_plans_json):
    result = {"frontend_action": "NONE", "products": []}
    if mode != "recommend" and not (mode == "lookup" and link_requested):
        return result
    plans = json.loads(selected_plans_json)
    if mode == "lookup" and len(plans) != 1:
        return result

    products = result["products"]
    seen = set()
    for plan in plans:
        code = plan.get("plan_code")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", code) or code in seen:
            continue
        seen.add(code)
        products.append({"plan_code": code, "link_label": plan.get("link_label") or ""})
        if len(products) == __RECOMMENDATION_LIMIT__:
            break
    labels = [re.sub(r"\s+", "", product["link_label"]).casefold() for product in products]
    for index, product in enumerate(products):
        if not labels[index] or labels.count(labels[index]) > 1:
            product["link_label"] = "查看計劃" if len(products) == 1 else f"查看第 {index + 1} 款"
    if products:
        result["frontend_action"] = "SHOW_PRODUCT_LINK"
    return result
'''


LABEL_VALIDATION_CODE = r'''def checked_link_label(label, markdown):
    if not isinstance(label, str):
        return ""
    label = label.strip()
    if not re.fullmatch(r"查看 [\w +＋·（）()-]{1,21}", label) or operator_reference(label):
        return ""
    name = label.removeprefix("查看 ").removesuffix("方案").removesuffix("計劃").strip()
    name = re.sub(r"\s+", "", name).casefold()
    source = re.sub(r"\s+", "", markdown).casefold()
    return label if name and name in source else ""


'''


def apply_product_link_policy(graph):
    nodes = {node["id"]: node for node in graph["nodes"]}
    if "product_link_action" in nodes:
        return

    def edit_code(node_id, old, new):
        data = nodes[node_id]["data"]
        data["code"] = replace(data["code"], old, new)

    def add_prompt(node_id, text, update_example=None):
        data = nodes[node_id]["data"]
        prompt = data["prompt_template"][0]
        if update_example:
            lines = prompt["text"].splitlines()
            for index, line in enumerate(lines):
                if not line.startswith("{") or "|" in line:
                    continue
                try:
                    example = json.loads(line)
                except json.JSONDecodeError:
                    continue
                update_example(example)
                lines[index] = json.dumps(example, ensure_ascii=False)
            prompt["text"] = "\n".join(lines)
            data["structured_output"]["schema"] = copy.deepcopy(MODEL_SCHEMAS[node_id])
        marker = "## 输出契约" if node_id == "reply" else "## 校验反馈重生成"
        prompt["text"] = replace(prompt["text"], marker, text.strip() + "\n\n" + marker)

    add_prompt("business", """
## 办理链接
- 客戶索取某款計劃嘅申請、登記或辦理連結時，按該計劃所屬業務走 shopping。用最近推薦或澄清語境理解指代。業務不明才先確認。
""")
    add_prompt("extract", """
## 本轮办理链接请求
- link_request 填最近一條客戶消息中明確索取某款辦理連結嘅逐字原話。普通查詳情、問有冇某類計劃、問優惠、拒絕接收連結及地址事件均填空字串。歷史消息唔可以單獨觸發。
- 純索取某款連結用 lookup，喺 question_changes 記錄本輪想辦理嘅計劃；唔新增購買條件。若上輪就辦理連結明確問邊一款，客戶今輪答「第2款」之類，視為承接，link_request 填今輪選擇嘅原話。

例：上輪比較兩款寬頻，客戶問「俾條第2款嘅申請連結我」。
{"changes":[],"question_changes":[{"business":"BN","operation":"add","question_id":"","text":"索取上一輪第2款計劃的辦理連結","evidence":["俾條第2款嘅申請連結我"]}],"clarification":"","reason":"客戶已指定第2款","context_update":null,"turn_intent":"lookup","link_request":"俾條第2款嘅申請連結我","availability_scope":"catalog","turn_conditions":[],"temporarily_excluded_ids":[],"purchase_switch":null}
""", lambda example: example.setdefault("link_request", ""))
    for node_id in ("extract_validation", "merge"):
        edit_code(node_id, '"temporarily_excluded_ids", "purchase_switch"}',
                  '"temporarily_excluded_ids", "purchase_switch", "link_request"}')
        edit_code(node_id, '        turn_intent = output.get("turn_intent", "shop")', '''        link_request = output.get("link_request", "")
        if not isinstance(link_request, str) or (link_request and not link_request.strip()):
            raise ValueError("办理链接请求必须为原话或空文本")
        if link_request and (not pending or link_request not in pending[-1]):
            link_request = ""
        turn_intent = output.get("turn_intent", "shop")''')
        edit_code(node_id, "temporarily_excluded_ids=excluded_ids)",
                  "temporarily_excluded_ids=excluded_ids, link_request=link_request)")

    edit_code("decision_state", '    if needs["turn_intent"] == "lookup":',
              '    needs["link_requested"] = bool(bundle.get("link_request"))\n    if needs["turn_intent"] == "lookup":')
    add_prompt("shortlist", """
## 定位要办理的套餐
- lookup 且 needs_json.link_requested 為 true 時，只可以揀客戶明確指定或按對話唯一能定位嘅當前可售計劃。上一輪有兩款而客戶只講「嗰款」時，用 clarify 問邊款；「第 2 款」按上輪實際展示順序定位。禁止為咗發連結任揀一款或者改推其他計劃。
""")
    for node_id in ("shortlist_validation", "product_decision"):
        edit_code(node_id, '        if bundle["instruction"] and action != "clarify" and not (action == "recommend" and can_check):',
                  '''        if bundle["instruction"] and action != "clarify" and (
            bundle.get("link_request") or not (action == "recommend" and can_check)
        ):''')

    def update_select_example(example):
        for assessment in example.get("assessments", []):
            assessment.setdefault("link_label", "")

    add_prompt("select", """
## 前端链接短文案
- 每款 assessment 嘅 link_label 可填「查看 1000M 方案」呢類短文案，格式係「查看」加空格再加該款原文已有嘅簡短規格或名稱，可加「方案」或「計劃」。最多24字，兩款盡量用容易分辨嘅特色。唔好寫網址、優惠承諾、營運商品牌或資料冇列明嘅規格。冇合適短名就填空字串。
- 如果 lookup 問題只係要辦理連結，照常核對指定套餐；原文冇辦理網址時，該問題標記 unknown，唔編造網址。
""", update_select_example)
    for node_id in ("select_validation", "validate_selection"):
        edit_code(node_id, 'def main(llm_output, candidates_json, needs_json):',
                  LABEL_VALIDATION_CODE + 'def main(llm_output, candidates_json, needs_json):')
        edit_code(node_id, '"requirements", "info_answers", "reason"}',
                  '"requirements", "info_answers", "reason", "link_label"}')
        edit_code(node_id, "        answered = {}", "        answered = {}\n        labels = {}")
        edit_code(node_id, '            answered[code] = info_answers',
                  '            answered[code] = info_answers\n            labels[code] = checked_link_label(assessment.get("link_label"), by_code[code]["markdown"])')
        edit_code(node_id, '"info_answers": answered[code]} for code in eligible',
                  '"info_answers": answered[code], "link_label": labels[code]} for code in eligible')

    edit_code("reply_context", '    if bundle["mode"] == "recommend":', '''    link_request = bundle.get("link_request", "")
    link_requested = bool(link_request and link_request in message)
    if bundle["mode"] == "lookup" and link_requested:
        if len(bundle["selected_plans"]) == 1:
            instruction += "\\n客戶想要指定計劃嘅辦理連結，請自然回應。純索取連結時唔需要報讀原文有冇列網址；其他事實問題照常核實。"
        else:
            instruction += "\\n有多個可能指代，只問客戶想要邊款計劃嘅辦理連結。"
    if bundle["mode"] == "recommend":''')
    edit_code("reply_context", '        "persist_reply": bundle["persist_reply"],',
              '        "link_requested": link_requested,\n        "persist_reply": bundle["persist_reply"],')
    nodes["reply_context"]["data"]["outputs"]["link_requested"] = {"children": None, "type": "boolean"}
    add_prompt("reply", """
## 回复末尾的套餐入口
- 推薦時，如果需要一句收尾，就用客戶今輪最在意嘅條件點出選擇重點。避免固定引導句、重複列套餐或承諾一定可以辦理。
- 依照 instruction 處理指定計劃嘅連結請求。套餐入口由前端顯示，聊天正文只用自然語言，唔寫網址、連結佔位符或按鈕文案。
""")

    node_id = "product_link_action"
    record = nodes["record_reply"]
    position = {"x": record["position"]["x"], "y": record["position"]["y"] + 160}
    fields = {"mode": "string", "link_requested": "boolean", "selected_plans_json": "string"}
    graph["nodes"].append({
        "data": {
            "code": PRODUCT_LINK_CODE.replace("__RECOMMENDATION_LIMIT__", str(RECOMMENDATION_LIMIT)),
            "code_language": "python3",
            "outputs": {
                "frontend_action": {"children": None, "type": "string"},
                "products": {"children": None, "type": "array[object]"},
            },
            "selected": False, "title": "输出套餐办理链接动作", "type": "code",
            "variables": [
                {"value_selector": ["reply_context", name], "value_type": kind, "variable": name}
                for name, kind in fields.items()
            ],
        },
        "height": 52, "id": node_id, "position": position, "positionAbsolute": dict(position),
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": record["width"],
    })
    graph["edges"].append({
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": "answer", "targetType": "code"},
        "id": f"answer-source-{node_id}-target", "source": "answer", "sourceHandle": "source",
        "target": node_id, "targetHandle": "target", "type": "custom", "zIndex": 0,
    })
