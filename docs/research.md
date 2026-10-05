# Buffered research and memory diagnostics

Kiokuko owns this workflow inside the plugin. Hermes core is unchanged.

## Use

- `/kiokuko-research <request>` searches once (up to three URLs), fetches page text,
  synthesizes and reviews a structured answer, then sends only the final result.
- `/kiokuko-research status` reports the package and profile loaded in the current
  authenticated Gateway process. CLI `hermes kiokuko doctor` and
  `hermes kiokuko status` describe their own process, not a running Gateway.
- Read-only guidance: `skill_view("kiokuko-tools:memory-reasoning")`. Registered
  plugin Skills are explicitly loadable; they are not user profile Skill files.

In `<profile>/kiokuko/config.yaml`, `research.mode` accepts `off`, `command`
(default), or `auto`. Auto routes explicit research/source/current-fact requests
on Gateway only. Opinions, creative and hypothetical requests stay in normal
conversation. It does not detect all factual assertions. Existing Gateway
sender authentication, command policies and operator hooks apply before work.
Search and extraction use the configured Hermes web backends. Main
`model.provider` and `model.default` (or `model.model`) must identify a concrete
supported route. There is no model-provider fallback or compression-model reuse.

Maximum input is 2000 characters; three fetched pages retain 4000 characters each;
two model calls use at most 2000 output tokens each. A 50-second overall limit
returns an unverified status on failure. Network work already in progress may
finish after cancellation, but its answer is discarded and no subsequent model
stage starts once cancellation is observed. No partial model output is sent.
Search snippets alone are insufficient. A search-only backend requires a working
extraction backend. Missing sources, malformed output, invented source IDs,
unmatched quotes and incomplete model output produce an unverified response.
Exact quote matching is deterministic; semantic relevance, primary-source status
and currentness are additionally model-assessed, not mathematically guaranteed.

A research event is claimed before paid calls. Exact repeats reuse its receipt;
changed input under the same event conflicts. An interrupted event remains
interrupted: send a new message to retry. Receipts are private per-profile files
under `kiokuko/research/`; they contain the final reply, hashed binding and memory
selection count. This count is not proof that the model applied a memory. Research
selection is separate from signed ordinary-history delivery observation.

## Install and roll back

1. Build and test the sdist and its wheel in an isolated environment. Keep the
   previous wheel for rollback. No release version bump is included here.
2. On the target Gateway, record the Python executable, profile and current
   `/kiokuko-research status` (or existing diagnostics before initial installation).
3. Install the reviewed wheel using that environment's Python, then run
   `hermes kiokuko doctor` with that same profile. Resolve reported configuration
   errors before restarting. Do not enable the unrelated Kiokuko MCP executable.
4. Restart the operator-owned Gateway once; use authenticated status to confirm
   its loaded package path and version. The CLI installation alone proves neither.
5. Verify explicit research with `command` first. Enable `auto` only after checking
   the routing and command policy in the isolated profile. Restore `command` or
   `off` to disable auto routing. For code rollback reinstall the previous wheel
   and restart the same Gateway. This change adds no database migration.

No existing candidates are deleted or approved. Native memory files and
user-maintained Skills are preserved. Source tests and mocked model tests do not
prove live Discord delivery, live web-provider availability or paid model quality.
