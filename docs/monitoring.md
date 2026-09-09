# API monitoring and experience memory

The optional monitor records Hermes **middleware requests and responses** with the pinned OrcaReplay writer. It supports OpenAI-compatible Chat Completions and Hermes' `codex_responses` loop, including `openai-codex`, in CLI and Gateway. It does not intercept HTTP, record individual SSE chunks or SDK-internal retries, or provide exact replay. Hermes core, provider routing, and credentials are unchanged.

## Enable

Install Node.js 22.12 or later separately, then use the interactive Hermes CLI or a Gateway chat in the intended profile:

```text
/kiokuko-monitor enable
/kiokuko-monitor status
/kiokuko-monitor disable
```

With no arguments, the command shows status. Terminal commands also remain available:

```sh
hermes kiokuko monitor enable
hermes kiokuko monitor status
hermes kiokuko doctor
```

The package contains the writer bundle and a matching upstream Python reader. Enabling does not run npm or download dependencies. Normal Kiokuko works without Node; monitoring is disabled on existing profiles until explicitly enabled. Restarting Hermes loads the new plugin code after package installation.

Every completed, authenticated turn schedules asynchronous extraction, including ordinary conversation. The model may return no experience. **These are additional model calls**, separate from the main conversation and existing compaction. Long input is divided at event/text boundaries; the extraction job has a bounded deadline. Failed/oversized/missing captures are visible and are not silently treated as complete.

Extraction uses the configured `auxiliary.compression` provider/model, inheriting a concrete main `model.provider`/`model.default` (`model.model` is also accepted) when omitted or `auto`. It supports OpenAI clients and Hermes' Codex Responses adapter. Codex uses Hermes' configured OAuth credentials, request conversion and stream assembly; it requires an explicitly completed response, including when terminal output is null. With no concrete configured route it reports `EXPERIENCE_MODEL_UNCONFIGURED`; other client types report `EXPERIENCE_ROUTE_UNSUPPORTED`. There is no provider fallback or SDK retry. No model or API key is bundled.

For `openai-codex`, leave Hermes' API mode selection intact: an unset `model.api_mode` selects Responses. Do not force Chat Completions to enable monitoring. Unsupported capture modes retain their cause in the completed incomplete run's `error_code` and persist a status count once per such run. Old incomplete traces cannot be reconstructed by `retry`; verify new turns after installing the fix and restarting Hermes.

## What is stored

Each authenticated user turn gets a separate Orca run. API calls, reconstructed streaming responses, tool calls/results, usage when provided, errors and timing share that timeline. The reader validates the event hash and every referenced blob. Source commit and bundle/source checksums are in the package's `orca/pin.json`.

Only new user input, new assistant text and observed tool events feed extraction. Requests' historical messages, system prompts and Kiokuko tool results are excluded. Assistant-only evidence cannot establish an independent experience. Model summaries are not verification, even when their quotes match.

Experience memory keeps a short situation, observation summary, action summary, result, hypothesis and bounded evidence. Only a cited tool result with a numeric exit code can support a check's success/failure; otherwise the outcome is unknown. Extraction cannot confer user approval. Permanent instructions and user attributes remain on the existing approval path. Model classification and secret detection are conservative filters, not guarantees of semantic accuracy or perfect redaction.

CLI/DM experiences belong to the principal and workspace (principal only if no workspace). Group experiences belong to the conversation and workspace (conversation only if no workspace). No automatic profile-wide sharing occurs. Existing approved/file-verified memory has priority. Recall includes at most two experiences / 800 characters within the existing 2,200-character total budget, explicitly labeled as unverified historical examples. Recalled content does not count as independent corroboration. Independent new runs may renew an active experience's 90-day lifetime; recall does not.

## Inspect and recover

```sh
hermes kiokuko monitor runs
hermes kiokuko monitor show RUN_ID
hermes kiokuko monitor retry RUN_ID
hermes kiokuko monitor disable
```

Status distinguishes enabled/runtime-ready, observed runs, incomplete capture, extraction jobs, missing sources and the last successful extraction. No runs means capture has not yet been demonstrated. `retry` retries failed extraction of an intact managed run; it never replays an API or tool call. Disabling monitoring also disables automatic experience recall; approved/file-verified recall continues. Gateway status, enable and disable follow Hermes command permissions. Trace inspection, retry and purge remain terminal operations.

The queue is limited to 128 events / 16 MiB and each event payload to 1 MiB. Overflow, writer failure and invalid identity prevent incomplete runs from generating experiences. Cancellation and provider exceptions retain their original behavior; a recorder error never retries a provider or tool. The writer uses private child-process pipes, not a listening server. Extraction is serialized per profile using an OS lock, including after a timed-out model worker.

## Retention, deletion and backup

Trace files live in `$HERMES_HOME/kiokuko/traces/`, with 0700 directories and 0600 files. Authentication headers are excluded; Orca also redacts payloads before writing. Treat retained trace bodies as sensitive.

Traces have a maximum seven-day retention and a 1 GiB profile limit. Old completed/incomplete runs are removed first. An active recording that cannot fit is marked incomplete; the conversation continues. If an unprocessed source is removed, its extraction job is blocked.

```sh
hermes kiokuko monitor purge RUN_ID
hermes kiokuko purge MEMORY_ID
```

The first command removes the source trace and blocks its extraction job. It does not delete already derived memories. The second deletes the selected memory and evidence, blocks its source jobs, and keeps content-free receipts to prevent regeneration by reprocessing. Neither deletes Hermes history or existing backups/exports. Revision, correction, forget and expiry use the existing memory lifecycle.

A rewind expires unverified experiences supported by the session's old generation and blocks its extraction jobs, including already stored experiences. The host does not supply the exact surviving event boundary, so this conservatively invalidates the whole affected generation. Human-corrected or approved memories are preserved.

Backups contain the memory DB and provenance metadata, not trace bodies. Restoring a backup without its source traces displays missing sources; it does not fabricate or fetch them.

## Development

The upstream code is Apache-2.0; the bundle includes Orca and dependency license files. The TypeScript writer and Python reader are pinned to the same commit. The schema embedding adapter in `tools/orca/build.mjs` changes module packaging only; upstream writer/redaction/validation logic remains intact.

```sh
.venv/bin/python scripts/build_orca.py --fetch
.venv/bin/python -m pytest tests/integration/test_monitor.py -q
.venv/bin/python -m pytest tests/hermes_e2e/test_monitor_host.py -q
```

The host tests need permission to bind localhost. They use an isolated profile and deterministic local responses, not paid APIs. Passing these tests establishes the execution/data boundaries; it does not measure extraction quality with a real model or Telegram/Discord network delivery.
