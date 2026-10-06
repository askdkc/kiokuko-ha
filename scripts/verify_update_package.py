"""Install a local wheel in a disposable venv and exercise both pinned hosts.

Uses the current test environment's dependencies, isolated host profiles and
mock pip jobs. A real offline pip reinstall is separate from Gateway mocks.
No PyPI request, Telegram delivery or production profile is involved.
"""
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
SMOKE = """
import sys
from pathlib import Path
sys.path.extend(sys.argv[1:4])
import hermes_kiokuko
assert Path(hermes_kiokuko.__file__).is_relative_to(Path(sys.prefix))
from importlib.metadata import entry_points
assert any(e.name == 'kiokuko-tools' for e in entry_points(group='hermes_agent.plugins'))
assert any(e.name == 'kiokuko' for e in entry_points(group='hermes_agent.memory_providers'))
import pytest
from types import SimpleNamespace
from conftest import host
from test_gateway_commands import (
    gateway_commands, test_gateway_update_start_and_status,
    test_cli_update_four_operations_through_process_command,
    test_registered_sync_handler_returns_to_origin_task_from_executor)
root = Path(sys.argv[4])
with pytest.MonkeyPatch.context() as patch:
    h = host.__wrapped__(root, patch)
    binding = next(h)
    g = gateway_commands.__wrapped__(binding, patch, SimpleNamespace(param='telegram'))
    gateway = next(g)
    try:
        test_registered_sync_handler_returns_to_origin_task_from_executor(gateway)
        test_gateway_update_start_and_status(gateway, patch, root)
        test_cli_update_four_operations_through_process_command(binding, patch)
    finally:
        g.close()
        h.close()
print('PASS: installed wheel entrypoints, registry, real Gateway/CLI update/status/help/retry; mock jobs')
"""

UNSUPPORTED_SMOKE = """
import sys
from pathlib import Path
sys.path.extend(sys.argv[1:4])
import hermes_kiokuko
assert Path(hermes_kiokuko.__file__).is_relative_to(Path(sys.prefix))
from hermes_kiokuko.compatibility import check_host
from hermes_kiokuko.errors import KiokukoError
try:
    check_host(require_config=False)
except KiokukoError as error:
    assert error.code == 'UNSUPPORTED_HERMES', error.code
else:
    raise AssertionError('Python 3.14 must reject Hermes 0.21.0 before memory sync')
print('PASS: installed wheel rejects Hermes 0.21.0 on Python 3.14')
"""


def main():
    wheel = Path(sys.argv[1]).resolve()
    dependencies = sysconfig.get_path('purelib')
    with tempfile.TemporaryDirectory(prefix='kiokuko-update-wheel-') as temporary:
        root = Path(temporary).resolve()
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        for _ in range(2):
            subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-index',
                            '--no-deps', '--force-reinstall', str(wheel)], check=True)
        for version, directory in [('0.21.0', 'hermes'), ('0.21.4', 'hermes-0.21.4')]:
            profile = root / version
            profile.mkdir()
            env = os.environ.copy()
            env['KIOKUKO_HERMES_FIXTURE'] = version
            smoke = UNSUPPORTED_SMOKE if sys.version_info[:2] == (3, 14) and version == '0.21.0' else SMOKE
            subprocess.run([str(python), '-I', '-c', smoke, dependencies,
                            str(ROOT / '.cache' / directory),
                            str(ROOT / 'tests/hermes_e2e'), str(profile)],
                           env=env, cwd=root, check=True)
    print('PASS: disposable venv and real offline local-wheel pip reinstall')


if __name__ == '__main__':
    main()
