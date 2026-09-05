# Kiokuko(記憶庫) for Hermes Agent

[日本語版](README_ja.md)

Kiokuko(記憶庫) is a memory plugin for Hermes Agent. It separates memories by person, conversation, and workspace, and keeps model-generated proposals pending until a human approves them.

During compaction and at the end of a conversation, Kiokuko stores only facts that can be rechecked against project files or configuration. The database is stored at `$HERMES_HOME/kiokuko/kiokuko.db`.

Supported environments are Python 3.11–3.13 and Hermes 0.21.

## Install

Install Kiokuko from PyPI into the same Python environment used by Hermes. This is the standard Hermes installation layout:

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m pip install --upgrade hermes-kiokuko
```

Initialize the profile:

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
export HERMES_HOME="$HOME/.hermes"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

When `doctor` reports `ok: true`, restart Hermes. Native `MEMORY.md` and `USER.md` are disabled, but existing files are preserved.

## When the profile is wrong

If you see `active profile is 'main'` together with `Falling back to .../.hermes`, set the active profile explicitly and run setup again:

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
export HERMES_HOME="$HOME/.hermes/profiles/main"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

`HERMES_HOME` defines the profile boundary. Use the same value that Hermes uses to start the target profile; each profile has separate configuration, database, and sessions.

## Update

With v0.1.1 or later installed, run these commands inside an interactive Hermes CLI session:

```text
/kiokuko-update
/kiokuko-update status
```

The update runs in the background using Hermes's own Python interpreter, with the current profile explicitly passed as `HERMES_HOME`. It upgrades the PyPI package `hermes-kiokuko` (the repository is named `kiokuko-ha`). Profiles sharing that Python environment receive the same package update; profile settings and memory databases are left intact. Use `/kiokuko-update retry` after a failure. This administrative command is available in the local interactive CLI, not Telegram/Discord chats.

For the first upgrade from v0.1.0, or to update from a terminal:

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
export HERMES_HOME="$HOME/.hermes/profiles/main" # Use your actual profile path
"$HERMES_PY" -m pip install --upgrade hermes-kiokuko
"$HERMES_PY" -m hermes_kiokuko doctor
```

Wait for the update to finish, then restart the Hermes process. For Telegram/Discord, restart the Hermes Gateway process serving those chats: a new chat session does not reload the installed plugin code. No OS reboot is needed. Python 3.14 is outside the current support range.

## Explicit storage and approval

Send the entire message in this form to store the original text immediately. The body is limited to 600 characters.

```text
@kiokuko remember --scope principal
Reply in Japanese.
```

Model proposals and natural-language requests such as “remember this” become pending candidates. Review and approve them from the CLI:

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
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
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m hermes_kiokuko curation
# Or, when the venv bin directory is on PATH:
kioku-curation
```

See [curation details](docs/curation.md), [operational boundaries](docs/operations.md), and [verification](docs/verification.md).
