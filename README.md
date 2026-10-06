# Kiokuko(記憶庫) for Hermes Agent

[日本語版](README_ja.md)

Kiokuko(記憶庫) is a memory plugin for Hermes Agent. It separates memories by person, conversation, and workspace, and keeps model-generated proposals pending until a human approves them.

During compaction and at the end of a conversation, Kiokuko stores only facts that can be rechecked against project files or configuration. The database is stored at `$HERMES_HOME/kiokuko/kiokuko.db`.

Kiokuko supports Python 3.11–3.14 and Hermes 0.21, including the current PM source contract tested at `88c60858468d7adee27a752242c7c507fa4129d0`. Python 3.14 requires Hermes 0.21.4 or later in the 0.21 series; earlier hosts use an incompatible thread executor for memory synchronization.

## Install with current Hermes PM

Current Hermes manages plugin code and Python dependencies together. Install directly from this Git repository; no versioned archive or manual copy is needed. Use the `hermes` launcher belonging to the target Hermes installation. Installing into an old `venv/bin/python` does not add a PM workspace member.

Choose the procedure for the profile serving your CLI or Gateway. These examples use the standard `$HOME/.hermes` root. For a custom root, replace that prefix with the root used by your Hermes installation. The explicit `--profile` also prevents a saved active profile from redirecting the default-profile commands.

### Default profile

The default profile lives directly in `$HOME/.hermes`, not in `profiles/default`:

```sh
export HERMES_HOME="$HOME/.hermes"
hermes --profile default plugins install askdkc/kiokuko-ha --enable --yes-deps || exit 1
hermes --profile default kiokuko setup || exit 1
hermes --profile default kiokuko doctor --load-plugin || exit 1
```

After doctor reports `ok: true` and `command_registration.ok: true`, restart the Gateway serving this profile:

```sh
hermes --profile default gateway restart
```

### Named profile: main

For an existing profile named `main`, use `$HOME/.hermes/profiles/main`. If it does not exist yet, create it first with `HERMES_HOME="$HOME/.hermes" hermes --profile default profile create main` and configure it for your CLI or Gateway.

```sh
export HERMES_HOME="$HOME/.hermes/profiles/main"
hermes --profile main plugins install askdkc/kiokuko-ha --enable --yes-deps || exit 1
hermes --profile main kiokuko setup || exit 1
hermes --profile main kiokuko doctor --load-plugin || exit 1
```

After doctor reports `ok: true` and `command_registration.ok: true`, restart the Gateway serving `main`:

```sh
hermes --profile main gateway restart
```

For either profile, restart an interactive CLI to load the plugin; for a Gateway, check `/commands` in its chat after the restart. Terminal doctor does not prove that the running Gateway loaded the plugin. `setup` disables native MEMORY.md/USER.md use and preserves their files and existing Kiokuko data.

The install command follows the Git repository's default branch. This is a custom Git source; catalog listing is not required. `--yes-deps` explicitly approves dependency installation. If installation or dependency preparation fails, resolve that error before running setup.

If Kiokuko is already installed from Git, skip `plugins install` and use `hermes --profile default plugins update kiokuko-tools` or `hermes --profile main plugins update kiokuko-tools` with the corresponding `HERMES_HOME` above. Then run that profile's setup and doctor commands and restart its Hermes process. A manually copied archive has no tracked Git source; it needs replacement through Hermes's source-install procedure. `/kiokuko-update` in native mode gives the managed update instructions and never invokes pip.

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

If PM reports a damaged recorded environment, use `hermes --profile main pm repair`. For a tracked Git update, use `hermes --profile main plugins update kiokuko-tools`. Check errors before continuing. Then run `hermes --profile main kiokuko doctor --load-plugin`, restart the serving Gateway, and verify `/commands` and `/kiokuko-update status` in Discord. If a fresh Gateway still cannot activate a healthy selected environment, retain the startup evidence for a Hermes issue; do not inject `src` or pip-install into PM generations.

`runtime_provenance` separates the configured manifest, current disk version, loaded code fingerprint/version, distribution metadata and PM selection. Unavailable PM information is unknown. Terminal diagnostics always describe their own process. Managed chat status displays a short loaded fingerprint and restart advice without local paths. Equal version numbers alone do not prove that an update was loaded.

## Legacy pip installation (Hermes without PM)

The procedures below apply only to older Hermes installations with a directly managed Python venv. Do not use them to modify a current PM-managed environment.


Install Kiokuko into the Python environment that actually runs the target Hermes process. For Gateway chats, use the interpreter in that Gateway's launch command or service configuration, keeping its venv path. `$HOME/.hermes/hermes-agent/venv/bin/python` is correct only if the Gateway uses it; installing there does not affect a Gateway running another Python.

Replace the path below with that interpreter. Confirm its path and version before installing:

```sh
export HERMES_PY="/absolute/path/to/gateway/python"
"$HERMES_PY" -c 'import sys; print(sys.executable); print(sys.version)'
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
```

Initialize the profile:

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

When `doctor` reports `ok: true`, restart Hermes. Native `MEMORY.md` and `USER.md` are disabled, but existing files are preserved.

## When the profile is wrong

If you see `active profile is 'main'` together with `Falling back to .../.hermes`, set the active profile explicitly and run setup again:

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
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
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main" # Use your actual profile path
cd "$HOME/.hermes/hermes-agent" || exit 1
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
"$HERMES_PY" -m hermes_kiokuko doctor
```

Wait for the update to finish, then restart the Hermes process. For Telegram/Discord, restart the Hermes Gateway process serving those chats: a new chat session does not reload the installed plugin code. No OS reboot is needed. If a service manages the Gateway, verify its launch interpreter too: restarting from a different terminal Python does not change the service configuration.

### When `/kiokuko-update` returns `Unknown command`

The command is not registered in that Gateway. An unregistered update command cannot repair this state. Check configuration and registration from a terminal using the same Python and profile. This example targets `main`:

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
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
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
cd "$HOME/.hermes/hermes-agent" || exit 1
test -f hermes_yaml.py || { echo 'Hermes source is missing hermes_yaml.py'; exit 1; }
"$HERMES_PY" -c 'import hermes_yaml; from hermes_cli.plugins import get_plugin_commands; print(hermes_yaml.__file__)'
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
```

If Hermes requires this module but the file is absent, repair the missing or inconsistent Hermes source. If the file exists but dependencies such as `ruamel.yaml` are missing, repair them using Hermes's own installation procedure. Kiokuko does not inject an alternative YAML implementation or skip host compatibility checks. After the terminal checks pass, restart the target Gateway and verify commands in chat.

### Manual wheel installation and recovery

Reinstallation is **not required on every startup**. Use the normal update procedure for published releases. Use this procedure only to install a supplied wheel, including an unpublished fix or a replacement build with the same version. PyPI updates do not include unpublished repository changes.

Transfer the wheel to the machine running Hermes and set `WHEEL` to its actual path; replace `VERSION` below with the supplied filename's version. Use the target profile's `HERMES_HOME`. The `--no-deps` option assumes Kiokuko's dependencies are already installed in this Hermes environment; omit it for a first installation that needs dependencies.

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1

WHEEL="$HOME/hermes_kiokuko-VERSION-py3-none-any.whl"

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --no-deps --reinstall "$WHEEL"
else
  "$HERMES_PY" -m pip install --no-deps --force-reinstall "$WHEEL"
fi

"$HERMES_PY" -c 'from plugins.memory import find_provider_dir; p = find_provider_dir("kiokuko"); print(p); assert p is not None'
"$HERMES_PY" -m hermes_kiokuko doctor
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
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko pending
"$HERMES_PY" -m hermes_kiokuko approve CANDIDATE_ID
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
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko curation
# Or, when the venv bin directory is on PATH:
kioku-curation
```

See [curation details](docs/curation.md), [operational boundaries](docs/operations.md), and [verification](docs/verification.md).

## Optional API monitoring and experience memory

OrcaReplay records Hermes's OpenAI-compatible API requests and responses from the CLI and Gateway. With Node.js 22.12 or later installed, enable monitoring and check its status in the interactive Hermes CLI or a Gateway chat for the intended profile:

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

[Buffered research and diagnostics](docs/research.md): `/kiokuko-research <request>` / `/kiokuko-research status`.
