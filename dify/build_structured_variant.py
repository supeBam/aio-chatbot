"""Generate a structured-output variant without modifying the original chatflow."""

import copy
import json
from pathlib import Path

import yaml

if __package__:
    from .customer_turn_policy import apply_customer_turn_policy
    from .model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
    from .product_link_policy import apply_product_link_policy
else:
    from customer_turn_policy import apply_customer_turn_policy
    from model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
    from product_link_policy import apply_product_link_policy


DIRECTORY = Path(__file__).resolve().parent
SOURCE = DIRECTORY / "AIO Multi Business.txt"
DESTINATION = DIRECTORY / "AIO Multi Business Structured.yml"


def configure_customer_replies(flow, nodes):
    flow["workflow"]["features"]["opening_statement"] = (
        "你好！想了解家居寬頻、流動通訊或者影音串流計劃？我可以幫你比較。"
    )
    for node_id in ("select", "select_retry_loop"):
        node = nodes[node_id]["data"]
        node["title"] = node["title"].replace("最多三款", f"最多{RECOMMENDATION_LIMIT}款")
    select_prompt = nodes["select"]["data"]["prompt_template"][0]
    select_prompt["text"] = select_prompt["text"].replace("最多3款", f"最多{RECOMMENDATION_LIMIT}款")
    for node_id in ("select_validation", "validate_selection"):
        node = nodes[node_id]["data"]
        node["code"] = node["code"].replace("len(selected) > 3", f"len(selected) > {RECOMMENDATION_LIMIT}").replace(
            "最多三个唯一编号", f"最多{RECOMMENDATION_LIMIT}个唯一编号"
        )

    context = nodes["reply_context"]["data"]
    context["code"] = context["code"].replace(
        '            "\\n- **取舍提醒**：关键限制、未知条件和偏好缺口。"\n', ""
    ).replace("**核心内容**", "**計劃內容**").replace("**月费与合约**", "**月費及合約**").replace(
        "**相关特色**", "**附加服務**"
    ).replace("最后简短对比", "有兩款時可簡短比較")

    prompt = nodes["reply"]["data"]["prompt_template"][0]
    replacements = {
        "用简体中文自然简洁地回复。": (
            "所有面向客戶的回覆一律用香港繁體中文，語氣自然、簡潔，像香港電訊客服。"
            "使用家居寬頻、流動通訊、流動電話（智能裝置稱智能電話）、本地數據、漫遊數據、"
            "上台月費計劃、合約期、安裝費、回贈及串流服務等香港用語。"
            "即使用戶、歷史或資料使用簡體字或其他地區用語，也用香港用語轉述，"
            "例如把「手機／手机」寫成「流動電話」或「智能電話」。"
            "中性計劃名稱可轉為香港繁體，保留計劃編號、品牌專名、數字與單位。"
            "回覆以本輪答覆收結，省略測試或演示聲明。"
        ),
        "我可以根据套餐资料帮您比较流量、月费和合约": "我可以按計劃資料幫你比較數據用量、月費同合約期",
        "本轮已校验精选的最多三款套餐": f"本輪已核實的最多{RECOMMENDATION_LIMIT}款計劃",
        "priority=preferred的缺口作为取舍": "如客戶問到未涵蓋的偏好，直接交代有關資料",
        "说明相对上一轮对应规格的变化及费用、合约取舍": "簡述與上一輪相比的規格、月費及合約期變化",
        "推荐必须说明仅为模拟演示，不代表真实报价或可办理承诺。": "",
        "优先解释核心速度/流量/服务及费用取舍": "優先交代網速、數據用量、服務及月費",
        "完整展示本轮精选和模拟演示声明": "列出本輪核實的計劃",
        "这次的方案名义带宽从上一轮的1000M提高到2000M；以下同时列出月费和合约取舍，实际体验仍受环境等因素影响。": (
            "今次計劃嘅標示網速由上次嘅1000M提升到2000M，實際速度會受使用環境影響。"
        ),
        "这次精选仍是1000M，没有比上一轮提升带宽，不能满足您希望更快的调整。以下仅作未改善备选，并非更高速推荐。": (
            "今次睇到嘅計劃仍然係1000M，網速同上次一樣，未能滿足你想再快啲嘅要求。"
        ),
        "这款资料列明2000M，但目前无法确认您所比较方案的带宽，因此还不能说它比之前更快。": (
            "呢款計劃列明係2000M，但暫時未確認你想比較嗰款嘅網速。"
        ),
        "“**核心内容**”“**月费与合约**”“**相关特色**”“**取舍提醒**”": (
            "“**計劃內容**”“**月費及合約**”“**附加服務**”。必要收費、合約條件或與本輪需求直接相關的限制，"
            "寫入相應項目或直接回答客戶"
        ),
        "最后仅作简短比较": "有兩款時可簡短比較，比較完即可結束",
    }
    for old, new in replacements.items():
        prompt["text"] = prompt["text"].replace(old, new)


def build(flow):
    flow = copy.deepcopy(flow)
    flow["app"]["name"] += " - JSON结构化输出"
    graph = flow["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}

    for model_id, schema in MODEL_SCHEMAS.items():
        llm = nodes[model_id]
        data = llm["data"]
        data["model"]["completion_params"]["response_format"] = "json_object"
        data["structured_output_enabled"] = True
        data["structured_output"] = {"schema": copy.deepcopy(schema)}
        data["error_strategy"] = "fail-branch"
        loop = nodes[model_id + "_retry_loop"]["data"]
        loop["loop_count"] = 3
        loop["title"] = loop["title"].replace("最多重试1次", "最多重试2次")
        system_prompt = data["prompt_template"][0]
        system_prompt["text"] = system_prompt["text"].replace(
            "校验反馈重生成（最多1次）", "校验反馈重生成（最多2次）"
        )

        # A few original examples omit a field required by the existing schema.
        if model_id == "select":
            prompt = data["prompt_template"][0]
            lines = prompt["text"].splitlines()
            for index, line in enumerate(lines):
                if not line.strip().startswith("{") or "|" in line:
                    continue
                try:
                    example = json.loads(line)
                except json.JSONDecodeError:
                    continue
                example.pop("selected_plan_codes", None)
                for assessment in example.get("assessments", []):
                    assessment.setdefault("info_answers", [])
                lines[index] = json.dumps(example, ensure_ascii=False)
            prompt["text"] = "\n".join(lines)
            prompt["text"] += (
                "\n\nassessments按推荐顺序排列，每款只出现一次；plan_code必须逐字复制当前候选编号，"
                "不拼接、改写或添加后缀。代码将按此顺序提取精选编号，不额外输出编号列表。"
            )
            for validator_id in ("select_validation", "validate_selection"):
                validator = nodes[validator_id]["data"]
                validator["code"] = validator["code"].replace(
                    '{"selected_plan_codes", "assessments", "reason"}',
                    '{"assessments", "reason"}',
                ).replace(
                    'selected = output["selected_plan_codes"]',
                    'if not isinstance(output["assessments"], list) or any('
                    'not isinstance(item, dict) or not isinstance(item.get("plan_code"), str) '
                    'for item in output["assessments"]):\n'
                    '            raise ValueError("套餐核对列表或编号无效")\n'
                    '        selected = [item["plan_code"] for item in output["assessments"]]',
                ).replace(
                    "精选结果必须包含编号、逐需求核对和依据", "精选结果必须包含逐需求核对和依据"
                )

        validation = nodes[model_id + "_validation"]["data"]
        llm_input = next(item for item in validation["variables"] if item["variable"] == "llm_output")
        llm_input["value_selector"] = [model_id, "structured_output"]
        llm_input["value_type"] = "object"
        marker = "validate = main\n\ndef main("
        if validation["code"].count(marker) != 1:
            raise ValueError(f"Unexpected validation wrapper: {model_id}")
        before, after = validation["code"].split(marker)
        signature, body = after.split(":\n", 1)
        validation["code"] = (
            before + marker + signature + ":\n"
            + '    if not isinstance(llm_output, dict):\n'
            + '        raise ValueError("结构化输出必须为JSON对象")\n'
            + "    llm_output = json.dumps(llm_output, ensure_ascii=False)\n"
            + body
        )
        update = nodes[model_id + "_retry_update"]["data"]
        next(item for item in update["items"] if item["variable_selector"][1] == "current_text")["value"] = [
            model_id + "_validation", "text"
        ]

        failure_id = model_id + "_node_failure"
        failure_update_id = model_id + "_failure_update"
        loop_id = model_id + "_retry_loop"
        failure = copy.deepcopy(nodes[model_id + "_validation"])
        failure["id"] = failure_id
        failure["position"] = {"x": 506, "y": 220}
        failure["positionAbsolute"] = {
            "x": nodes[loop_id]["positionAbsolute"]["x"] + 506,
            "y": nodes[loop_id]["positionAbsolute"]["y"] + 220,
        }
        failure["data"].update({
            "title": "记录模型节点失败并重试",
            "code": (
                "def main(error_type, error_message):\n"
                '    return {"retry": True, "error": "模型节点失败（" + str(error_type) + "）："'
                ' + str(error_message), "text": ""}\n'
            ),
            "variables": [
                {"value_selector": [model_id, "error_type"], "value_type": "string", "variable": "error_type"},
                {"value_selector": [model_id, "error_message"], "value_type": "string", "variable": "error_message"},
            ],
        })
        failure_update = copy.deepcopy(nodes[model_id + "_retry_update"])
        failure_update["id"] = failure_update_id
        failure_update["position"] = {"x": 828, "y": 220}
        failure_update["positionAbsolute"] = {
            "x": nodes[loop_id]["positionAbsolute"]["x"] + 828,
            "y": nodes[loop_id]["positionAbsolute"]["y"] + 220,
        }
        failure_update["data"]["title"] = "更新模型失败重试状态"
        for item in failure_update["data"]["items"]:
            field = item["variable_selector"][1]
            item["value"] = [failure_id, {"current_text": "text", "last_error": "error", "retry": "retry"}[field]]
        graph["nodes"].extend([failure, failure_update])

        def edge(source, handle, target, source_type, target_type):
            return {
                "data": {"isInIteration": False, "isInLoop": True, "loop_id": loop_id,
                         "sourceType": source_type, "targetType": target_type},
                "id": f"{source}-{handle}-{target}-target", "source": source,
                "sourceHandle": handle, "target": target, "targetHandle": "target",
                "type": "custom", "zIndex": 1001,
            }

        graph["edges"].extend([
            edge(model_id, "fail-branch", failure_id, "llm", "code"),
            edge(failure_id, "source", failure_update_id, "code", "assigner"),
        ])

    configure_customer_replies(flow, nodes)
    apply_customer_turn_policy(nodes)
    apply_product_link_policy(graph)
    return flow


def main():
    flow = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    result = build(flow)
    DESTINATION.write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(DESTINATION)


if __name__ == "__main__":
    main()
