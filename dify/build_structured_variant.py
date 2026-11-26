"""从工作流画布、Python 规则和套餐资料生成 Dify 导入文件。"""

import argparse
import ast
import copy
from pathlib import Path

import yaml

if __package__:
    from .model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
    from .plan_catalog import load_catalogs
else:
    from model_output_schemas import MODEL_SCHEMAS, RECOMMENDATION_LIMIT
    from plan_catalog import load_catalogs


DIRECTORY = Path(__file__).resolve().parent
SOURCE = DIRECTORY / "workflow_graph.yml"
LOGIC = DIRECTORY / "workflow_logic.py"
DESTINATION = DIRECTORY / "AIO Multi Business Structured.yml"
FORWARD_NODES = {"basic_reply", "skip_analysis"}


class WorkflowDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def represent_text(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


WorkflowDumper.add_representer(str, represent_text)


def node_sources(source):
    """按源码顺序收集函数和常量，供独立的 Dify 沙箱使用。"""
    definitions = {}
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.FunctionDef):
            names = [statement.name]
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            names = [statement.targets[0].id]
        elif isinstance(statement, ast.Import) or isinstance(statement, ast.ImportFrom) and statement.level == 0:
            names = [item.asname or item.name.split(".")[0] for item in statement.names]
        else:
            continue
        code = ast.get_source_segment(source, statement)
        definitions.update(dict.fromkeys(names, code))
    return definitions


def render_code(definitions, node):
    node_id, data = node["id"], node["data"]
    entry = node_id
    if node_id in FORWARD_NODES:
        entry = "forward_bundle"
    elif node_id.endswith("_node_failure"):
        entry = "model_failure"

    adapt_bundle = node_id not in FORWARD_NODES
    reads_bundle = adapt_bundle and any(v["variable"] == "bundle" for v in data["variables"])
    writes_bundle = adapt_bundle and "bundle" in data["outputs"]
    if reads_bundle or writes_bundle:
        # 业务函数在 Python 内使用对象。只有代码节点入口转换分支数据。
        function = ast.parse(definitions[entry]).body[0]
        signature = ast.unparse(function.args)
        arguments = [arg.arg for arg in function.args.args]
        if reads_bundle:
            arguments[arguments.index("bundle")] = "unpack_bundle(bundle)"
        wrapper = f"def main({signature}):\n    result = {entry}({', '.join(arguments)})"
        if writes_bundle:
            wrapper += '\n    result["bundle"] = pack_bundle(result["bundle"])'
        wrapper += "\n    return result"
    else:
        wrapper = f"main = {entry}"

    needed = set()

    def collect(code):
        for name in {token.id for token in ast.walk(ast.parse(code)) if isinstance(token, ast.Name)}:
            if name in definitions and name not in needed:
                needed.add(name)
                collect(definitions[name])

    collect(wrapper)
    parts = dict.fromkeys(code for name, code in definitions.items() if name in needed)
    return "\n\n\n".join([*parts, wrapper]) + "\n"


def build(flow):
    flow = copy.deepcopy(flow)
    catalogs, details = load_catalogs()
    workflow = flow["workflow"]
    bundle_fields = {
        item["name"]: {"type": item["value_type"], "children": None}
        for item in workflow["conversation_variables"]
    }
    bundle_fields.update({
        "mode": {"type": "string", "children": None},
        "context_json": {"type": "string", "children": None},
    })
    definitions = {
        "TAG_CATALOGS": f"TAG_CATALOGS = {catalogs!r}",
        "DETAILS": f"DETAILS = {details!r}",
        "RECOMMENDATION_LIMIT": f"RECOMMENDATION_LIMIT = {RECOMMENDATION_LIMIT}",
        **node_sources(LOGIC.read_text(encoding="utf-8")),
    }
    for node in workflow["graph"]["nodes"]:
        data = node["data"]
        if data["type"] == "code":
            data["code"] = render_code(definitions, node)
            if "bundle" in data["outputs"]:
                data["outputs"]["bundle"]["children"] = copy.deepcopy(bundle_fields)
        elif node["id"] in MODEL_SCHEMAS:
            data["structured_output"] = {"schema": copy.deepcopy(MODEL_SCHEMAS[node["id"]])}
    return flow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="检查提交的导出文件与源码是否一致")
    args = parser.parse_args()
    flow = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    export = yaml.dump(build(flow), Dumper=WorkflowDumper, allow_unicode=True, sort_keys=False, width=120)
    for path in (DESTINATION, DESTINATION.with_suffix(".txt")):
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != export:
                parser.exit(1, f"{path.name} 与源码不一致，请重新构建\n")
        else:
            path.write_text(export, encoding="utf-8")
            print(path)


if __name__ == "__main__":
    main()
