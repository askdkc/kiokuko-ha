# Kiokuko(記憶庫) for Hermes Agent

[日本語版](README_ja.md)

Kiokuko(記憶庫) is a memory plugin for Hermes Agent. It separates memories by person, conversation, and workspace, and keeps model-generated proposals pending until a human approves them.

During compaction and at the end of a conversation, Kiokuko stores only facts that can be rechecked against project files or configuration. The database is stored at `$HERMES_HOME/kiokuko/kiokuko.db`.

Kiokuko requires Python 3.11 or later for the standard-library `tomllib`, with no upper version cap. Hermes compatibility is checked through the required memory/plugin APIs and an in-memory synchronization probe. PyYAML has no upper version cap; safe configuration roundtrips and rejection of Python-object tags are checked by behavior. Release numbers and source fingerprints do not decide admission. The probe detects broken background executors, including the older executor on Python 3.14. See [verification](docs/verification.md) for tested hosts and limits.

## Install with current Hermes PM

Current Hermes manages plugin code and Python dependencies together. Install directly from this Git repository; no versioned archive or manual copy is needed. Use the `hermes` launcher belonging to the target Hermes installation. Installing into an old `venv/bin/python` does not add a PM workspace member.

Choose the procedure for the profile serving your CLI or Gateway. These examples use the standard `$HOME/.hermes` root. For a custom root, replace that prefix with the root used by your Hermes installation. The explicit `--profile` also prevents a saved active profile from redirecting the default-profile commands.

Check the source with `plugins list` in the target profile. A standard Git installation lives at `$HERMES_HOME/plugins/kiokuko-tools/`, so the update commands below use `kiokuko-tools` directly. The argument to `plugins update` is a directory relative to `$HERMES_HOME/plugins/`; the repository source `askdkc/kiokuko-ha` and Python distribution `hermes-kiokuko` are different identifiers.

- Standard Git installation: use the update block below as written. If its directory differs, see the exception below.
- No matching installation: check the profile and `hermes` launcher first; use first installation only if it is absent from this profile.
- `entrypoint` only (legacy pip installation): this is not a downloaded plugin. Choose first Git installation below or the legacy Hermes pip procedure later in this README, according to the serving installation.
- `bundled`: individual `plugins update` is unavailable; update Hermes itself.
- Manually copied tree without Git or a recorded source: `update` cannot pull it. Preserve user files and use Hermes' source reinstall procedure; do not add `--force` unconditionally. Pinned installations require an intentional `--ref` change.

Parentheses below run a subshell. A failed command stops the `&&` chain before setup/doctor and leaves your login shell running.

### Default profile

The default home is `$HOME/.hermes`, not `profiles/default`.

First installation:

```sh
(
  export HERMES_HOME="$HOME/.hermes"
  hermes --profile default plugins install askdkc/kiokuko-ha --enable --yes-deps &&
  hermes --profile default plugins list --no-bundled &&
  hermes --profile default kiokuko setup &&
  hermes --profile default kiokuko doctor --load-plugin
)
```

Update an existing standard Git installation:

```sh
(
  export HERMES_HOME="$HOME/.hermes"
  hermes --profile default plugins update kiokuko-tools &&
  hermes --profile default kiokuko setup &&
  hermes --profile default kiokuko doctor --load-plugin
)
```

After doctor reports `ok: true` and `command_registration.ok: true`, restart the serving Gateway:

```sh
HERMES_HOME="$HOME/.hermes" hermes --profile default gateway restart
```

### Named profile: main

Use an existing main profile. If absent, first create it with `HERMES_HOME="$HOME/.hermes" hermes --profile default profile create main` and configure it.

First installation:

```sh
(
  export HERMES_HOME="$HOME/.hermes/profiles/main"
  hermes --profile main plugins install askdkc/kiokuko-ha --enable --yes-deps &&
  hermes --profile main plugins list --no-bundled &&
  hermes --profile main kiokuko setup &&
  hermes --profile main kiokuko doctor --load-plugin
)
```

Update an existing standard Git installation:

```sh
(
  export HERMES_HOME="$HOME/.hermes/profiles/main"
  hermes --profile main plugins update kiokuko-tools &&
  hermes --profile main kiokuko setup &&
  hermes --profile main kiokuko doctor --load-plugin
)
```

If the update asks `Prepare these with Hermes through PM now? [y/N]:` for PyYAML or other dependencies, review the list and type `y` followed by Enter to prepare them. Enter alone declines; dependency preparation and the update will not proceed. Hermes owns this PM dependency consent prompt. The first-install `--yes-deps` option gives advance consent for Python dependencies; `plugins update` has no such option.

Only if the installation directory differs from the standard path: use `plugins list --no-bundled` in the same profile and `find "$HERMES_HOME/plugins" -maxdepth 3 -name plugin.yaml -print` to confirm it, then replace `kiokuko-tools` in the update command with that relative directory. For example, `.../plugins/kiokuko-ha/plugin.yaml` means `kiokuko-ha`; `.../plugins/custom/kiokuko-ha/plugin.yaml` means `custom/kiokuko-ha`. The manifest display name alone does not establish the directory.

After doctor reports `ok: true` and `command_registration.ok: true`, restart the serving Gateway:

```sh
HERMES_HOME="$HOME/.hermes/profiles/main" hermes --profile main gateway restart
```

Restart an interactive CLI too. After a Gateway restart verify `/commands` and `/kiokuko-update status`; terminal doctor does not prove what a running Gateway loaded. `setup` disables native MEMORY.md/USER.md use while preserving their files and existing Kiokuko data.

Git installation follows the repository default branch; catalog listing is not required. `--yes-deps` explicitly consents to dependency installation. Resolve install/update failures before setup. Native `/kiokuko-update` provides download-key guidance and never runs pip.

For nested installations, a full key is necessary for lookup, but some Hermes PM versions refuse publication with `plugin publication paths escape or overlap their home`. Stop on that error; migration to a supported flat installation is needed. Do not edit PM internals or treat the refused update as successful.

### If update refuses a changed installed name

`The updated plugin changed its installed name; reinstall it explicitly.` is different from an incorrect key. The old directory name differs from the new manifest name, so normal update cannot migrate it. Use the following explicit reinstall **only after observing that error**. It backs up the whole old plugin inside the profile before removing and reinstalling through Hermes. Review user settings/additional files in the backup and restore only the needed ones. Memory data under `$HERMES_HOME/kiokuko/` is outside the removed plugin directory. This example targets default; for main change both the home and `KIOKUKO_PROFILE`.

```sh
(
  export HERMES_HOME="$HOME/.hermes"
  KIOKUKO_PROFILE=default
  hermes --profile "$KIOKUKO_PROFILE" plugins list --no-bundled &&
  find "$HERMES_HOME/plugins" -maxdepth 3 -name plugin.yaml -print &&
  printf 'Old downloaded plugin directory key (empty to stop): ' &&
  read -r KIOKUKO_OLD_KEY &&
  [ -n "$KIOKUKO_OLD_KEY" ] &&
  KIOKUKO_BACKUP="$(mktemp -d "$HERMES_HOME/kiokuko-plugin-backup.XXXXXX")" &&
  cp -R "$HERMES_HOME/plugins/$KIOKUKO_OLD_KEY" "$KIOKUKO_BACKUP/plugin" &&
  printf 'Backup: %s\n' "$KIOKUKO_BACKUP" &&
  hermes --profile "$KIOKUKO_PROFILE" plugins remove "$KIOKUKO_OLD_KEY" &&
  hermes --profile "$KIOKUKO_PROFILE" plugins install askdkc/kiokuko-ha --enable --yes-deps &&
  hermes --profile "$KIOKUKO_PROFILE" kiokuko setup &&
  hermes --profile "$KIOKUKO_PROFILE" kiokuko doctor --load-plugin
)
```

The official catalog is a separate human-reviewed submission. This repository is not claimed to be catalog-listed or catalog-approved; legacy pip self-update code also requires a separate catalog-policy review. See [Hermes plugin documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/) and [catalog submission requirements](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission/).

### PM installation: `Unknown command` or missing module

An enabled plugin is configuration, not proof of loading. If the Gateway logs `No module named 'hermes_kiokuko'`, use host commands first; the missing Kiokuko command cannot repair itself. For `main`, use the launcher belonging to the serving installation:

```sh
export HERMES_HOME="$HOME/.hermes/profiles/main"
hermes --profile main plugins list
hermes --profile main pm status
hermes --profile main pm doctor
```

Compare the Gateway startup diagnostic's Python, prefix, profile, plugin path and relevant search paths with the PM selection. The same Python executable can load different PM environments. A successful doctor in an old venv proves only that old process.

If PM reports a damaged recorded environment, use `hermes --profile main pm repair`. For a tracked Git update, use the main-profile update block above. Only installations with a different directory need the argument replacement described above. Check errors before continuing. Then run `hermes --profile main kiokuko doctor --load-plugin`, restart the serving Gateway, and verify `/commands` and `/kiokuko-update status` in Discord. If a fresh Gateway still cannot activate a healthy selected environment, retain the startup evidence for a Hermes issue; do not inject `src` or pip-install into PM generations.

`runtime_provenance` separates the configured manifest, current disk version, loaded code fingerprint/version, distribution metadata and PM selection. Unavailable PM information is unknown. Terminal diagnostics always describe their own process. Managed chat status displays a short loaded fingerprint and restart advice without local paths. Equal version numbers alone do not prove that an update was loaded.

## Legacy pip installation (Hermes without PM)

The procedures below apply only to older Hermes installations with a directly managed Python venv. Do not use them to modify a current PM-managed environment.


Install Kiokuko into the Python environment that actually runs the target Hermes process. For Gateway chats, use the interpreter in that Gateway's launch command or service configuration, keeping its venv path. `$HOME/.hermes/hermes-agent/venv/bin/python` is correct only if the Gateway uses it; installing there does not affect a Gateway running another Python.

Replace the path below with that interpreter. Confirm its path and version before installing:

```sh
export HERMES_PY="/absolute/path/to/gateway/python"
"$HERMES_PY" -c 'import sys; print(sys.executable); print(sys.version)' &&
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
```

Initialize the profile:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes"
cd "$HOME/.hermes/hermes-agent"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
)
```

Restart Hermes after setup and start a new session. `doctor` separates configuration and database checks from live session visibility; a fresh doctor process reports the existing chat as unconfirmed. To inspect registration and search in the doctor process, use `doctor --load-plugin --platform cli` (or `telegram`). If memory is disabled, explicitly opt in with `setup --enable-memory-toolset cli`; global disables and individual exclusions are preserved. See [tool publication diagnostics](docs/operations.md#正規ツールが検索で見つからない場合). Native `MEMORY.md` and `USER.md` are disabled, but existing files are preserved.

## When the profile is wrong

If you see `active profile is 'main'` together with `Falling back to .../.hermes`, set the active profile explicitly and run setup again:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
)
```

`HERMES_HOME` defines the profile boundary. Use the same value that Hermes uses to start the target profile; each profile has separate configuration, database, and sessions.

## Update

For current PM installations, use the profile-specific `plugins update` procedure in [Install with current Hermes PM](#install-with-current-hermes-pm).

### Legacy pip update (Hermes without PM)

After the Kiokuko command plugin has loaded, run these commands in the interactive Hermes CLI or a Gateway chat such as Discord or Telegram:

```text
/kiokuko-update
/kiokuko-update status
```

The update runs in the background using Hermes's own Python interpreter, with the current profile explicitly passed as `HERMES_HOME`. It upgrades the PyPI package `hermes-kiokuko` (the repository is named `kiokuko-ha`). Profiles sharing that Python environment receive the same package update; profile settings and memory databases are left intact. Use `/kiokuko-update retry` after a failure. Gateway access follows Hermes command permissions.

`/kiokuko-update` requires `pip` in Hermes's Python environment. If it is unavailable, use the terminal procedure below with `uv`.

For the first upgrade from v0.1.0, or to update from a terminal:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main" # Use your actual profile path
cd "$HOME/.hermes/hermes-agent"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
"$HERMES_PY" -m hermes_kiokuko doctor
)
```

Wait for the update to finish, then restart the Hermes process. For Telegram/Discord, restart the Hermes Gateway process serving those chats: a new chat session does not reload the installed plugin code. No OS reboot is needed. If a service manages the Gateway, verify its launch interpreter too: restarting from a different terminal Python does not change the service configuration.

### When `/kiokuko-update` returns `Unknown command`

The command is not registered in that Gateway. An unregistered update command cannot repair this state. Check configuration and registration from a terminal using the same Python and profile. This example targets `main`:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
)
```

`--load-plugin` loads configured Hermes plugins into this terminal process and checks ownership and registration of all four Kiokuko commands. Ordinary `doctor` does not load plugins and reports `checked: false` for command registration. Neither proves what the running Gateway loaded. The `enabled` label in `hermes plugins list` also describes configuration, rather than successful loading.

After both `command_registration.ok: true` and the overall `ok: true`, restart the Gateway serving the same profile:

```sh
"$HERMES_PY" -m hermes_cli.main --profile main gateway restart
```

Check that `/commands` in the chat lists `kiokuko-update`. If terminal registration succeeds but the Gateway still lacks the command, check the serving process's Python, profile, and restart target. If registration fails, inspect its diagnostics and the Gateway startup log for `Failed to load plugin 'kiokuko-tools'`. Older releases without `--load-plugin` need the normal terminal update above before this check. Missing `pip` affects the update after registration; it does not explain `Unknown command`.

### When `hermes_yaml` is missing

`hermes_yaml` is Hermes's own `hermes_yaml.py`, not a Kiokuko dependency. If an editable install created before a Hermes update cannot find new root modules, Kiokuko adds the Hermes source tree belonging to the installed `hermes_cli` to the import path. It does not use files in the working directory or profile as a recovery source.

If loading still fails, check the Python and checkout used to run Hermes.

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
cd "$HOME/.hermes/hermes-agent"
if [ ! -f hermes_yaml.py ]; then
  printf '%s\n' 'Hermes source is missing hermes_yaml.py'
  false
fi
"$HERMES_PY" -c 'import hermes_yaml; from hermes_cli.plugins import get_plugin_commands; print(hermes_yaml.__file__)'
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
)
```

If Hermes requires this module but the file is absent, repair the missing or inconsistent Hermes source. If the file exists but dependencies such as `ruamel.yaml` are missing, repair them using Hermes's own installation procedure. Kiokuko does not inject an alternative YAML implementation or skip host compatibility checks. After the terminal checks pass, restart the target Gateway and verify commands in chat.

### Manual wheel installation and recovery

Reinstallation is **not required on every startup**. Use the normal update procedure for published releases. Use this procedure only to install a supplied wheel, including an unpublished fix or a replacement build with the same version. PyPI updates do not include unpublished repository changes.

Transfer the wheel to the machine running Hermes and set `WHEEL` to its actual path; replace `VERSION` below with the supplied filename's version. Use the target profile's `HERMES_HOME`. The `--no-deps` option assumes Kiokuko's dependencies are already installed in this Hermes environment; omit it for a first installation that needs dependencies.

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent"

WHEEL="$HOME/hermes_kiokuko-VERSION-py3-none-any.whl"

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --no-deps --reinstall "$WHEEL"
else
  "$HERMES_PY" -m pip install --no-deps --force-reinstall "$WHEEL"
fi

"$HERMES_PY" -c 'from plugins.memory import find_provider_dir; p = find_provider_dir("kiokuko"); print(p); assert p is not None'
"$HERMES_PY" -m hermes_kiokuko doctor
)
```

For a first installation, run `setup` for the target profile before `doctor`, as shown above. A detected directory confirms discovery in this CLI process; it does not prove the running Gateway loaded the package. After installation succeeds and `doctor` reports `ok: true`, restart the Hermes process serving the target profile. If detection returns `None` or `doctor` fails, see [provider discovery and host diagnostics](docs/operations.md).

## Explicit storage and approval

Send the entire message in this form to store the original text immediately. The body is limited to 600 characters.

```text
@kiokuko remember --scope principal
Reply in Japanese.
```

Model proposals and natural-language requests such as “remember this” become pending candidates. Review and approve them from the CLI:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko pending
"$HERMES_PY" -m hermes_kiokuko approve CANDIDATE_ID
)
```

`kioku-curation` rechecks verified project memories and lets you share selected items as Global memories within the same profile.

From an interactive Hermes CLI session in the project directory (v0.1.1+):

```text
/kioku-curation
/kioku-curation select 1 3
/kioku-curation share
/kioku-curation confirm CODE
```

Replace `CODE` with the confirmation code shown after `share`. Use `cancel` to exit without sharing. Global memories are shared with every user and project in the profile. Gateway chats (DM/group) cannot use this administrative flow; run it from the local CLI. The terminal command is also available:

```sh
(
set -e
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko curation
# Or, when the venv bin directory is on PATH:
kioku-curation
)
```

See [curation details](docs/curation.md), [operational boundaries](docs/operations.md), and [verification](docs/verification.md).

## Optional API monitoring and experience memory

OrcaReplay records Hermes's OpenAI-compatible API requests and responses from the CLI and Gateway. With a Node.js runtime that passes the bundled-writer protocol check installed, enable monitoring and check its status in the interactive Hermes CLI or a Gateway chat for the intended profile:

```text
/kiokuko-monitor enable
/kiokuko-monitor status
```

Stop with `/kiokuko-monitor disable`.

Every completed turn schedules additional model extraction (up to four windows); useful experiences can be recalled automatically as **unverified historical examples**, separately from approved memory. Monitoring is initially disabled; traces are retained for at most seven days / 1 GiB per profile. See [setup, model routing, boundaries and deletion](docs/monitoring.md). Optional derived lessons add evidence-based updates and conditional recall, with learning initially **off**. See [learning controls and evaluation](docs/learning.md).

## Optional task-profile memory

Local task-profile memory can recall explicit targets from completed CLI/DM requests without extra model calls. It defaults to off. Enable collection without displaying hints, then inspect its status:

```sh
python -m hermes_kiokuko task-profiles mode shadow
python -m hermes_kiokuko task-profiles status
```

[Setup, modes, limits and deletion](docs/task-profiles.md)

[Source-chasing research and diagnostics](docs/research.md): ordinary human `web_search` calls and `/kiokuko-research <request>` trace references to checked original excerpts, with bounded exploration and explicit unresolved questions. `/kiokuko-research status` reports the loaded runtime.
