"""JSON Schema contracts for internal LLM nodes; the final reply remains text."""


def object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def array_schema(items: dict, limit: int) -> dict:
    return {"type": "array", "items": items, "maxItems": limit}


def enum_schema(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string"}
RECOMMENDATION_LIMIT = 2
BUSINESS = enum_schema("BN", "MS", "XE")
EVIDENCE = {"type": "array", "items": TEXT, "minItems": 1}
CHANGE_FIELDS = {
    "business": BUSINESS, "operation": enum_schema("add", "update", "remove"),
    "requirement_id": TEXT, "text": TEXT,
    "attitude": enum_schema("interested", "uninterested", "indifferent"),
    "priority": enum_schema("required", "preferred", "unspecified"), "evidence": EVIDENCE,
}
QUESTION_FIELDS = {
    "business": BUSINESS, "operation": enum_schema("add", "update", "remove"),
    "question_id": TEXT, "text": TEXT, "evidence": EVIDENCE,
}
FOLLOW_UP = object_schema({
    "topic": TEXT, "information_needed": TEXT, "selection_impact": TEXT,
    "blocking": {"type": "boolean"},
})
MODEL_SCHEMAS = {
    "business": object_schema({
        "business": enum_schema("", "BN", "MS", "XE"),
        "mentioned_businesses": array_schema(BUSINESS, 3),
        "clarification": TEXT, "intent": enum_schema("consult", "shopping", "boundary"),
        "business_requests": array_schema(object_schema({
            "business": BUSINESS, "messages": EVIDENCE,
        }), 3),
    }),
    "extract": object_schema({
        "turn_intent": enum_schema("shop", "lookup", "availability"),
        "link_request": TEXT,
        "availability_scope": enum_schema("catalog", "current_purchase"),
        "turn_conditions": array_schema(object_schema({"text": TEXT, "evidence": EVIDENCE}), 20),
        "temporarily_excluded_ids": array_schema(TEXT, 100),
        "purchase_switch": {"anyOf": [
            {"type": "null"}, object_schema({"target": TEXT, "evidence": EVIDENCE}),
        ]},
        "changes": array_schema(object_schema(CHANGE_FIELDS), 50),
        "question_changes": array_schema(object_schema(QUESTION_FIELDS), 50),
        "clarification": TEXT, "reason": TEXT,
        "context_update": {"anyOf": [
            {"type": "null"}, object_schema({"value": TEXT, "evidence": EVIDENCE}),
        ]},
    }),
    "shortlist": object_schema({
        "action": enum_schema("recommend", "interview", "clarify", "boundary"),
        "selected_plan_codes": array_schema(TEXT, 5),
        "follow_up": {"anyOf": [{"type": "null"}, FOLLOW_UP]},
        "reason": TEXT,
    }),
    "select": object_schema({
        "assessments": array_schema(object_schema({
            "plan_code": TEXT,
            "link_label": TEXT,
            "requirements": array_schema(object_schema({
                "requirement_id": TEXT, "status": enum_schema("supported", "unsupported", "unknown"),
                "quote": TEXT, "explanation": TEXT,
            }), 100),
            "info_answers": array_schema(object_schema({
                "question_id": TEXT, "status": enum_schema("answered", "unknown"),
                "quote": TEXT, "answer": TEXT,
            }), 100),
        }), RECOMMENDATION_LIMIT),
        "reason": TEXT,
    }),
}
