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

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m pip install --upgrade hermes-kiokuko
"$HERMES_PY" -m hermes_kiokuko doctor
```

Restart Hermes after updating. Python 3.14 is outside the current support range.

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

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m hermes_kiokuko curation
# Or, when the venv bin directory is on PATH:
kioku-curation
```

See [curation details](docs/curation.md), [operational boundaries](docs/operations.md), and [verification](docs/verification.md).
