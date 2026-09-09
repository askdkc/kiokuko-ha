"""Responses text and a single configured Hermes Codex extraction request."""
import time

from .errors import KiokukoError


def assistant_text(response):
    """Read visible assistant text only; never reasoning or function-call arguments."""
    parts = []
    for item in response.get('output') or []:
        if not isinstance(item, dict) or item.get('type') != 'message' or item.get('role') != 'assistant':
            continue
        for part in item.get('content') or []:
            if isinstance(part, dict) and part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                parts.append(part['text'])
    return '\n'.join(parts)


def extract_response(client, model, kwargs):
    # Use Hermes' route-specific request conversion, OAuth client and stream assembler.
    # Do not call its auxiliary fallback/retry ladder or change the configured provider.
    from agent.auxiliary_client import _CodexCompletionsAdapter, _CodexStreamGuard, aux_stream_deadline
    from agent.codex_runtime import _bypass_sdk_request_transform, _consume_codex_event_stream
    from .monitor_capture import payload

    bounded = client._real_client.with_options(timeout=10, max_retries=0)
    adapter = _CodexCompletionsAdapter(bounded, model)
    request, _, _ = adapter._build_responses_kwargs({**kwargs, 'timeout': 10})
    # Hermes otherwise extends its deadline while tokens arrive. This extraction
    # keeps a fixed ceiling, inside the job's existing 12-second commit deadline.
    with aux_stream_deadline(time.monotonic() + 10):
        guard = _CodexStreamGuard(bounded, 10)
    completed = False

    def observe(event):
        nonlocal completed
        guard.on_event(event)
        completed = completed or getattr(event, 'type', None) == 'response.completed'

    try:
        guard.start()
        stream = bounded.responses.create(**_bypass_sdk_request_transform({**request, 'stream': True}))
        guard.adopt_stream(stream)
        try:
            guard.check_cancelled()
            if hasattr(stream, 'output'):
                response = stream
                completed = getattr(response, 'status', None) == 'completed'
            else:
                response = _consume_codex_event_stream(stream, model=request['model'], on_event=observe)
        finally:
            guard.release_stream(stream)
        # Hermes can reconstruct status=completed after EOF with no terminal event.
        # Extraction must not accept even valid-looking JSON from a truncated stream.
        if not completed or getattr(response, 'status', None) != 'completed':
            raise KiokukoError('EXPERIENCE_INCOMPLETE')
        return assistant_text(payload(response))
    finally:
        guard.finish()
