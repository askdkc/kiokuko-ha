from .errors import KiokukoError, public_error
from .models import canonical
from .tool_context import require_context


def _handler(name, args, session_id, task_id):
    try:
        ctx = require_context(name, session_id, task_id)
        service, snap = ctx.service, ctx.snapshot
        if not isinstance(args, dict):
            raise KiokukoError("INVALID_ARGUMENTS")
        action = args.get("action", "search" if name == "kiokuko_recall" else "propose")
        if name == "kiokuko_propose":
            result = service.propose(snap, args)
        elif name == "kiokuko_manage":
            if action == "feedback":
                if set(args) - {"action", "entry_id", "revision", "delivery_id", "verdict"}:
                    raise KiokukoError("INVALID_ARGUMENTS")
                service.feedback(snap, args.get("entry_id"), args.get("revision"), args.get("delivery_id"), args.get("verdict"))
                result = {"recorded": True}
            elif action in {"pin_request", "unpin_request", "expire_request"}:
                result = service.propose(snap, args)
            else:
                raise KiokukoError("INVALID_ACTION")
        elif name == "kiokuko_recall":
            if set(args) - {"action", "query", "entry_id"}:
                raise KiokukoError("INVALID_ARGUMENTS")
            if action in {"search", "conflicts"}:
                result = service.search(snap, args.get("query", ""), conflicts=action == "conflicts")
            elif action in {"get", "history"}:
                result = service.get(snap, args.get("entry_id"), history=action == "history")
            else:
                raise KiokukoError("INVALID_ACTION")
            from .deliveries import record_manual_read
            record_manual_read(service, snap, result)
        return canonical({"ok": True, "data": result})
    except Exception as error:
        return public_error(error)


def recall_handler(args, *, task_id=None, session_id=None, user_task=None):
    return _handler("kiokuko_recall", args, session_id, task_id)


def propose_handler(args, *, task_id=None, session_id=None, user_task=None):
    return _handler("kiokuko_propose", args, session_id, task_id)


def manage_handler(args, *, task_id=None, session_id=None, user_task=None):
    return _handler("kiokuko_manage", args, session_id, task_id)
