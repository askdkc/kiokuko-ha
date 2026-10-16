# Source-chasing research and memory diagnostics

Kiokuko owns this workflow inside the plugin. Hermes core is unchanged.

## Use

- Ordinary Hermes `web_search` on a bound human CLI or Gateway turn runs the
  imozuru adaptation below. This works in the default `command` mode; users do
  not need to remember a Skill name or slash command. The original tool query
  starts the first route, and the authenticated user's request defines coverage.
- `/kiokuko-research <request>` runs the same orchestrator and returns checked
  original excerpts, extracted-text line locations, unresolved questions and the
  stopping reason. It buffers the result until exploration and review finish.
- `/kiokuko-research status` reports the package and profile loaded in the current
  authenticated Gateway process. CLI `hermes kiokuko doctor` and
  `hermes kiokuko status` describe their own process, not a running Gateway.
- Read-only guidance: `skill_view("kiokuko-tools:kiokuko-imozuru")` and
  `skill_view("kiokuko-tools:memory-reasoning")`. Registered
  plugin Skills are explicitly loadable; they are not user profile Skill files.

In `<profile>/kiokuko/config.yaml`, `research.mode` accepts `off`, `command`
(default), or `auto`. Off leaves ordinary search unchanged and disables the
command. Command enables explicit commands and the ordinary web-search adapter.
Auto additionally routes explicit research/source/current-fact/search requests
on Gateway only. Opinions, creative and hypothetical requests stay in normal
conversation. It does not detect all factual assertions. Existing Gateway
sender authentication, command policies and operator hooks apply before work.
Search and extraction use the configured Hermes web backends. Main
`model.provider` and `model.default` (or `model.model`) must identify a concrete
supported route. There is no model-provider fallback or compression-model reuse.

The orchestrator plans 3–6 necessary questions (the first preserves the exact
request), searches two complementary routes, maps terms/people/venues, and follows
references, citations, authors, terms and venues grounded in fetched text.
For a narrow single fact or navigation request the planner selects `lookup`:
one question, one search query (two hits), up to three attempted URLs and two
passes. Unfamiliar fields and original-source hunts select full exploration.
Markdown extraction retains links, including relative URLs. Original evidence
requires exact nonempty quotes, a quoted basis for primary status and a separate
semantic review. Source IDs, fetched URLs, relationships, search routes, rounds,
failures, evidence line locations and unresolved questions form the result ledger.
The command displays excerpts, not a model-generated conclusion.

Maximum input is 2000 characters. Budgets are four rounds, six distinct search
queries (three hits each), twelve attempted source URLs and 16000 retained
characters per source. Failed attempts consume budget. URLs are canonicalized,
fragments/tracking parameters removed, and duplicate queries/content/URLs are
suppressed. Only public HTTP(S), without credentials or secret query parameters,
is eligible; actual network and redirect protection remains in Hermes' extractor.
Private URLs are never fetched by an alternative downloader. Sources are fetched
one at a time so an observable redirect destination can be attributed to the
requested URL, checked and deduplicated. Hidden redirect hops that the backend
does not expose cannot be independently audited by this plugin.

At most six model stages (plan, four passes, final review) use the main configured
model, each with a 2000-token output cap. The orchestrator checks a 45-second
deadline between stages; both outer entry points stop waiting at 50 seconds.
Network work already in progress may finish after cancellation/timeout, but its
answer is discarded and no subsequent stage starts once cancellation is observed.
The per-profile worker lock remains held until such work ends. No partial model
output is sent.
Search snippets alone are insufficient. A search-only backend requires a working
extraction backend. Missing sources, malformed output, invented source IDs,
unmatched quotes and incomplete model output cannot establish coverage.
Exact quote matching is deterministic; semantic relevance, primary-source status
and currentness are additionally model-assessed, not mathematically guaranteed.
Stopping means all necessary questions have reviewed evidence, no new grounded
work remains, or an explicit budget was reached. Saturation is no-new-work, not
an estimate that all relevant literature was found. Every uncovered question
remains unresolved, including when the final review removes candidate evidence.

The normal tool result retains `success` and `data.web`, with only reviewed primary
sources in `data.web`. `research` contains the ledger, exact excerpts and source
digests; full extracted documents are not persisted by this plugin. Line numbers
refer to retained extraction text, not original PDF pagination. Hermes backends
may independently cache/store extraction text according to their own settings.
An ephemeral per-profile, per-session, per-turn binding reuses one investigation
for repeated or changed search queries in that turn, including failed calls.
Parallel calls cannot start duplicate investigations. New messages get a new
budget. The bounded binding cache retains at most 128 turns in process memory.

The adapter runs after Hermes' tool admission and existing authenticated snapshot
hook. Sender/profile/generation changes invalidate its binding. Unbound,
background, cron and delegated turns retain ordinary tool behavior. Non-search
tools, memory recall and shell commands pass through unchanged. Current hosts
expose `web_search` directly and reject using it through `tool_call`; hosts that
allow deferral unwrap the actual tool before middleware. The main Hermes model
still generates ordinary conversation answers; this is a verified tool-evidence
path, not a global final-answer rejection hook.

A research event is claimed before paid calls. Exact repeats reuse its receipt;
changed input under the same event conflicts. An interrupted event remains
interrupted: send a new message to retry. Receipts are private per-profile files
under `kiokuko/research/`; they contain the final reply, hashed binding and memory
selection count. This count is not proof that the model applied a memory. Scoped
memories are not sent as search queries or primary evidence. Research
selection is separate from signed ordinary-history delivery observation.

The method is adapted from [MaryCache/imozuru](https://github.com/MaryCache/imozuru).
See [provenance and full MIT license](../skills/kiokuko-imozuru/references/NOTICE.md).
The Skill and notice ship in wheel/sdist and are registered read-only. The
upstream OpenAlex/RSS/OCR/transcript/report scripts are not copied or executed;
this integration uses the configured Hermes web backends. No research output or
user feedback is automatically saved as a Kiokuko memory entry.

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
