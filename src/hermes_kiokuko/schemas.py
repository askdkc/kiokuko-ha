def schema(name, description, properties):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": ["action"], "additionalProperties": False}}


RECALL_SCHEMA = schema("kiokuko_recall", "Read scoped historical memory. Current evidence overrides it.", {
    "action": {"type": "string", "enum": ["search", "get", "history", "conflicts"]},
    "query": {"type": "string", "maxLength": 600}, "entry_id": {"type": "string"},
})
PROPOSE_SCHEMA = schema("kiokuko_propose", "Create a pending candidate only. A human must approve the displayed claim in CLI. Quote matching is not approval.", {
    "action": {"type": "string", "enum": ["propose", "correct", "forget_request"]},
    "claim": {"type": "string", "maxLength": 600},
    "scope": {"type": "string", "enum": ["principal", "principal_workspace", "conversation", "conversation_workspace"]},
    "entry_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 1},
    "evidence_quote": {"type": "string", "maxLength": 600},
    "kind": {"type": "string", "enum": ["statement", "identity", "preference", "constraint", "environment_fact", "project_fact", "decision", "lesson", "milestone"]},
    "subject_key": {"type": "string", "maxLength": 600},
})
MANAGE_SCHEMA = schema("kiokuko_manage", "Scoped feedback or pending pin/unpin/expiry requests. Cannot approve or purge.", {
    "action": {"type": "string", "enum": ["feedback", "pin_request", "unpin_request", "expire_request"]},
    "entry_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 1},
    "revision": {"type": "integer", "minimum": 1}, "delivery_id": {"type": "string"},
    "verdict": {"type": "string", "enum": ["helpful", "irrelevant", "stale", "conflicting"]},
})
