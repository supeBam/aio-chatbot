"""Generate a structured-output variant without modifying the original chatflow."""

import copy
import json
from pathlib import Path

import yaml

if __package__:
    from .model_output_schemas import MODEL_SCHEMAS
else:
    from model_output_schemas import MODEL_SCHEMAS


DIRECTORY = Path(__file__).resolve().parent
SOURCE = DIRECTORY / "AIO Multi Business.txt"
DESTINATION = DIRECTORY / "AIO Multi Business Structured.yml"


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
