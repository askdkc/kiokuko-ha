"""Public errors deliberately carry no user payload, SQL, or filesystem paths."""

import json


class KiokukoError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def public_error(error: Exception) -> str:
    code = error.code if isinstance(error, KiokukoError) else "INTERNAL_ERROR"
    return json.dumps({"ok": False, "error": code})
