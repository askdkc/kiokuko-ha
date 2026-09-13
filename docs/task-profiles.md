# Task-profile memory

Task-profile memory records short, authenticated user requests that name an existing file or directory. Later requests can receive a target suggestion, or a resolved target reference when one explicit identifier maps unambiguously to a current path. It uses local SQLite queries and filesystem checks; it adds no LLM calls and does not require API monitoring or experience learning.

The default is **off**. This feature does not block the conversation with an intake questionnaire. It does not change the user's message, tool arguments, working directory, or permissions. Past mentions are not approvals or evidence that a task succeeded.

## Enable and inspect

Run the commands with the Python environment and `HERMES_HOME` used by the intended Hermes profile. Install the updated package and restart that Hermes process first. For a Gateway, restart the process handling that Gateway; opening another chat does not reload plugin code.

```sh
# Run in the Python environment where hermes-kiokuko is installed.
python -m hermes_kiokuko task-profiles mode shadow
python -m hermes_kiokuko task-profiles status
python -m hermes_kiokuko task-profiles list
```

| Mode | Capture | Model-visible behavior |
|---|---|---|
| `off` | Disabled | No new hints; necessary corrections for earlier hints remain |
| `shadow` | Enabled | Record decisions without adding hints |
| `suggest` | Enabled | Show possible targets and their source identifiers |
| `resolve` | Enabled | Resolve only an unambiguous, current exact target; otherwise suggest |

After inspecting the collected records and shadow results:

```sh
python -m hermes_kiokuko task-profiles mode suggest
python -m hermes_kiokuko task-profiles status
# Enable limited automatic target references when desired:
python -m hermes_kiokuko task-profiles mode resolve
python -m hermes_kiokuko task-profiles status
```

Mode changes are read on subsequent operations. Each turn keeps its original decision: switching modes does not recompute that turn's candidates. Switching to off suppresses new hints, including replays. A turn evaluated in shadow remains invisible even if the setting changes later.

To stop capture and hints:

```sh
python -m hermes_kiokuko task-profiles mode off
python -m hermes_kiokuko task-profiles status
```

The equivalent configuration is in the profile's `kiokuko/config.yaml`. Quote `off` because the YAML loader also recognizes an unquoted `off` as a boolean.

```yaml
task_profile_memory:
  mode: "off"
```

## Example and limits

With collection enabled, a completed request such as `Inspect src/widget.py` records the explicit path if it exists under the turn's bound workspace. A later request containing `widget.py` can receive `src/widget.py` as a target reference. Backticks make identifiers explicit and support Unicode paths, such as `日本語/例.py`.

An explicit current path, including a nonexistent path such as `new/widget.py`, is never replaced with a different historical path. A current existing `widget.py` also takes precedence over a historical `src/widget.py`. Competing paths with the same basename prevent automatic resolution. Suggestions remain historical references to be checked against the current request.

- Capture requires an authenticated, completed CLI or DM turn and a bound workspace. It does not use assistant text or injected memory as user evidence.
- Records are scoped to the same Hermes profile, principal, and workspace. Groups, cron, delegated work, background reviews, and unknown identities do not collect or receive profiles.
- The stored excerpt is at most 600 characters, with at most eight explicit identifiers. Secrets and unsafe input are rejected before saving the excerpt. Long or ambiguous requests are not a source of invented success criteria or constraints.
- Lookup expands at most 64 profiles and displays at most three target hints in 400 characters. Exact matches take priority over lexical fallback. Partial searches cannot automatically resolve a target.
- Existing memory and correction text have priority within the existing overall context budget. A valid hint may therefore be omitted when the budget is full.
- File checks do not follow symlinks or read file contents. Missing, moved, or out-of-scope targets are not resolved.
- Completion means the host completed processing the turn; it does not prove the task succeeded.
- No older Hermes chat archive is backfilled. Records start after the feature is enabled.

## Review, delete, and rebuild

These commands are local administrator operations, not Gateway slash commands or model tools. The list displays the source excerpt and targets. Use its actual record ID in the delete command:

```sh
python -m hermes_kiokuko task-profiles list
python -m hermes_kiokuko task-profiles delete PROFILE_ID
```

Deletion displays the exact record and requires typing its ID. Cancellation leaves it intact. The reviewed record is checked again before deletion.

Deletion clears the live profile text, targets, indexes, and content-derived digest. Only content-free retry receipts and delivery references remain. They prevent a delayed completion from recreating the deleted record and allow signed corrections for hints already delivered. Hermes history and existing backups are outside this logical deletion.

Records expire after 30 days, with at most 1,000 live records per profile. Search excludes expired records immediately. Bounded cleanup runs during collection and administration; a dormant profile's expired bytes are removed when cleanup next runs. Rewinding a source session invalidates and removes its profiles. Workspace relinking invalidates affected source records.

```sh
python -m hermes_kiokuko task-profiles reindex
python -m hermes_kiokuko task-profiles status
```

Reindex rebuilds only the stored task profiles in small batches. If interrupted, its projection stays marked partial, which prevents automatic resolution. Rerun the command to finish. FTS is optional; n-gram lookup remains available without it.

## Storage and recovery

The package migrates existing schema v1–v4 databases to v5 under the existing migration lock and integrity checks. Existing migration files and their checksums are unchanged. Both new SQL files are included in the distribution.

Use the existing `backup` and `restore` commands before upgrading when a rollback is required. Switching the feature off does not make a v5 database readable by an old package. Restoring an older backup uses the existing same-profile/key checks; never copy just the database file from a running WAL database. Restore retains the existing requirement that live users close and checkpoint the database first.

A failed optional capture is reported through status codes and does not undo turn completion. Deadline, identity, and integrity errors are not reported as successful empty searches. A failed database transaction is rolled back before any later operation uses another connection.

## Verification and measurements

```sh
.venv/bin/python -m pytest tests/integration/test_task_profiles.py tests/integration/test_retrieval_equivalence.py -q
.venv/bin/python -m pytest tests/hermes_e2e/test_task_profiles_host.py -q
.venv/bin/python scripts/evaluate_profile_memory.py
.venv/bin/python scripts/benchmark_profile_memory.py
.venv/bin/python scripts/benchmark_profile_memory.py --entries 100000 --profiles 10000 --output artifacts/profile-memory-benchmark-large.json
```

Evaluation uses 30 finite resolver cases. Boundary tests separately cover scope, signed delivery, deletion, generation, migration rollback, and real pinned Hermes CLI/DM agent loops with deterministic HTTP responses. These tests do not measure real-model reasoning quality, live Telegram delivery, or task-wide token savings.

The benchmark creates a disposable synthetic database; it never reads a real profile. Its 10,000-profile option is a stress fixture beyond the production retention cap. Reports include Python/SQLite versions, sample count, first-sample timing, p50/p95, SQL count, and preparation stages. The OS file cache is not flushed. Results are local measurements rather than performance guarantees.

Recorded on 2026-09-13 with Python 3.12.13, SQLite 3.53.4, macOS arm64, and 10 samples per operation:

| Synthetic records / profiles | Previous search p50 | Updated search p50 | Prepare off p50 | Prepare resolve p50 |
|---|---:|---:|---:|---:|
| 10,000 / 1,000 | 8.19 ms | 5.66 ms | 14.56 ms | 54.67 ms |
| 100,000 / 10,000 | 67.05 ms | 31.66 ms | 98.28 ms | 182.17 ms |

Search results matched in both fixtures, and the exact target reached all 10 resolve deliveries in each run. The separate finite resolver evaluation reported 0 errors and 0 false adoptions across 30 cases. These runs shared the machine with tests; they are not isolated performance measurements. SQL statement count increased by one despite the lower search time. Full reports are in `artifacts/profile-memory-benchmark.json`, `artifacts/profile-memory-benchmark-large.json`, and `artifacts/profile-memory-evaluation.json`.
