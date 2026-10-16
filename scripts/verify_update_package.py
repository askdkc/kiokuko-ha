"""Install a local wheel in a disposable venv and exercise explicitly selected hosts.

Uses the current test environment's dependencies, isolated host profiles and
mock pip jobs. A real offline pip reinstall is separate from Gateway mocks.
No PyPI request, Telegram delivery or production profile is involved.
"""
import os
import argparse
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--host', type=Path, action='append', help='Current host checkout; repeat to test multiple hosts')
    target.add_argument('--fixture', action='append', help='Opt-in historical fixture; repeat for a regression matrix')
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    dependencies = sysconfig.get_path('purelib')
    with tempfile.TemporaryDirectory(prefix='kiokuko-update-wheel-') as temporary:
        root = Path(temporary).resolve()
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        for _ in range(2):
            subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-index',
                            '--no-deps', '--force-reinstall', str(wheel)], check=True)
        targets = [(host.resolve(), None) for host in args.host or []]
        for fixture in args.fixture or []:
            directory = 'hermes' if fixture == '0.21.0' else 'hermes-' + fixture
            targets.append((ROOT / '.cache' / directory, fixture))
        for index, (host, fixture) in enumerate(targets):
            profile = root / str(index)
            profile.mkdir()
            env = os.environ.copy()
            env.pop('KIOKUKO_HERMES_FIXTURE', None)
            if fixture:
                env['KIOKUKO_HERMES_FIXTURE'] = fixture
            subprocess.run([str(python), '-I', '-c', SMOKE, dependencies,
                            str(host),
                            str(ROOT / 'tests/hermes_e2e'), str(profile)],
                           env=env, cwd=root, check=True)
    print('PASS: disposable venv and real offline local-wheel pip reinstall')


if __name__ == '__main__':
    main()
