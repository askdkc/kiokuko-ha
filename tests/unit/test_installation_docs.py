"""Pasting installation/update examples must never terminate a login shell."""
from pathlib import Path
import os
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['README.md', 'README_ja.md'])
def test_documented_plugin_commands_fail_without_exiting_shell(name, tmp_path):
    text = (ROOT / name).read_text()
    assert text.count('plugins update kiokuko-tools &&') == 2
    assert 'KIOKUKO_PLUGIN_KEY' not in text
    assert '|| exit' not in text
    blocks = [b for b in re.findall(r'```sh\n(.*?)```', text, re.S)
              if 'plugins install ' in b or 'plugins update ' in b]
    assert len(blocks) == 5  # two profiles plus explicit name-migration recovery
    binary = tmp_path / 'bin'; binary.mkdir()
    fake = binary / 'hermes'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit 17\n')
    fake.chmod(0o755)
    log = tmp_path / 'calls'
    env = dict(os.environ, HOME=str(tmp_path), PATH=str(binary) + os.pathsep + os.environ['PATH'], CALL_LOG=str(log))
    for block in blocks:
        assert 'exit' not in block
        log.unlink(missing_ok=True)
        result = subprocess.run(['sh', '-c', block + '\nprintf "SHELL_ALIVE\\n"\n'],
                                env=env, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0 and 'SHELL_ALIVE' in result.stdout
        calls = log.read_text().splitlines()
        assert len(calls) == 1 and 'kiokuko setup' not in calls[0] and 'kiokuko doctor' not in calls[0]


@pytest.mark.parametrize('name', ['README.md', 'README_ja.md'])
@pytest.mark.parametrize('profile', ['default', 'main'])
@pytest.mark.parametrize('fail_step', [None, 0, 1, 2])
def test_standard_update_is_pasteable_and_stops_on_failure(name, profile, fail_step, tmp_path):
    text = (ROOT / name).read_text()
    blocks = [b for b in re.findall(r'```sh\n(.*?)```', text, re.S)
              if f'hermes --profile {profile} plugins update ' in b]
    assert len(blocks) == 1
    block = blocks[0]
    assert 'read ' not in block and 'find ' not in block
    commands = [f'--profile {profile} plugins update kiokuko-tools',
                f'--profile {profile} kiokuko setup',
                f'--profile {profile} kiokuko doctor --load-plugin']
    binary = tmp_path / 'bin'; binary.mkdir()
    fake = binary / 'hermes'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" __CALL__ "$HERMES_HOME" "$@" >> "$CALL_LOG"\n'
                    'if [ "$*" = "$FAIL_COMMAND" ]; then exit 17; fi\n')
    fake.chmod(0o755)
    home = tmp_path / 'home with spaces'; home.mkdir()
    log = tmp_path / 'calls'
    env = dict(os.environ, HOME=str(home), PATH=str(binary), CALL_LOG=str(log),
               FAIL_COMMAND=commands[fail_step] if fail_step is not None else '')
    subprocess.run(['/bin/sh', '-n', '-c', block], check=True, env=env)
    result = subprocess.run(['/bin/sh', '-c', block + '\nprintf "SHELL_ALIVE\\n"\n'],
                            env=env, stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == 'SHELL_ALIVE\n'
    expected_home = home / '.hermes'
    if profile != 'default':
        expected_home = expected_home / 'profiles' / profile
    expected_commands = commands if fail_step is None else commands[:fail_step + 1]
    expected = [arg for cmd in expected_commands for arg in ['__CALL__', str(expected_home), *cmd.split()]]
    assert log.read_text().splitlines() == expected


@pytest.mark.parametrize('name', ['README.md', 'README_ja.md', 'docs/operations.md'])
def test_missing_python_guard_is_confined_to_subshell(name, tmp_path):
    text = (ROOT / name).read_text()
    assert '|| exit' not in text
    blocks = [b for b in re.findall(r'```sh\n(.*?)```', text, re.S) if '${HERMES_PY:?' in b]
    for block in blocks:
        assert block.startswith('(\n')
        env = {k: v for k, v in os.environ.items() if k != 'HERMES_PY'}
        result = subprocess.run(['sh', '-c', block + '\nprintf "SHELL_ALIVE\\n"\n'], env=env,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0 and 'SHELL_ALIVE' in result.stdout
