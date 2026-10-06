"""Exercise public PM installation and cold boots in a disposable Hermes home.

Requires a provisioned Python with host dependencies. Uses real PM/tool downloads;
never adds Kiokuko source/site-packages to PYTHONPATH. No external chat transport.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import selectors
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''
import hermes_bootstrap
from hermes_kiokuko.config import setup
from hermes_kiokuko.compatibility import active_home
from hermes_cli.plugins import get_plugin_manager
home = active_home()
setup(home)
manager = get_plugin_manager()
manager.discover_and_load()
loaded = manager._plugins['kiokuko-tools']
assert loaded.enabled and not loaded.error
assert len(loaded.tools_registered) == 3
assert len(loaded.commands_registered) == 4
from plugins.memory import load_memory_provider
provider = load_memory_provider('kiokuko')
provider.initialize('pm-cold-boot', hermes_home=str(home))
provider.shutdown()
from hermes_kiokuko.provenance import runtime_provenance
print('READY', flush=True)
import sys, json
for line in sys.stdin:
    if line.strip() == 'status':
        info = runtime_provenance(loaded.manifest)
        assert '読込済み' in manager._plugin_commands['kiokuko-update']['handler'].execute(home, 'status')
        print(json.dumps(info), flush=True)
    else:
        break
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    pin = json.loads((ROOT / 'tests/hermes_e2e/pin.json').read_text())['fixtures']['current-88c6085']
    import hashlib
    for name, digest in pin['files'].items():
        assert hashlib.sha256((host / name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory(prefix='pm-lifecycle-', dir=ROOT / '.cache') as directory:
        root = Path(directory)
        home = root / '.hermes'
        home.mkdir()
        other = home / 'profiles' / 'other'
        other.mkdir(parents=True)
        (other / 'config.yaml').write_text('plugins: {enabled: [], disabled: []}\n')
        other_config = (other / 'config.yaml').read_bytes()
        copy = home / 'hermes-agent'
        shutil.copytree(host, copy, ignore=shutil.ignore_patterns('__pycache__', '.git', '.venv', 'venv'))
        # Seed only the host's initial dependencies; PM owns all plugin generations.
        (copy / '.venv').symlink_to(Path(sys.prefix), target_is_directory=True)
        with tarfile.open(args.archive) as source:
            source.extractall(root / 'source', filter='data')
        plugin = next((root / 'source').iterdir())
        # Local Git fixture; never changes the user's repository/history.
        subprocess.run(['git', 'init', '-q', str(plugin)], check=True)
        subprocess.run(['git', '-C', str(plugin), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(plugin), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'PYTHON', 'UV_', 'PIP_'))
               and k not in ('VIRTUAL_ENV',) and not k.endswith(('_TOKEN', '_SECRET', '_PASSWORD', '_API_KEY'))}
        env.update(HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(root / 'tools'),
                   PYTHONPATH=str(copy), UV_CACHE_DIR=str(ROOT / '.cache/uv-native'))
        def cli(*arguments, success=True):
            result = subprocess.run([sys.executable, str(copy / 'hermes_cli/main.py'),
                                     '--profile', 'default', *arguments], env=env, cwd=root,
                                    text=True, capture_output=True, timeout=600)
            if success and result.returncode:
                raise RuntimeError(f'public command {arguments} failed: {result.stderr[-3000:]} {result.stdout[-3000:]}')
            return result
        cli('plugins', 'install', plugin.as_uri(), '--enable', '--yes-deps')
        probe = subprocess.run([sys.executable, '-c',
            'import hermes_bootstrap; from hermes_cli._launchers import runtime_command; '
            'from pathlib import Path; import sys, json; '
            'print(json.dumps(runtime_command(Path(sys.argv[1]), code=sys.argv[2])))', str(copy), PROBE],
            env=env, cwd=root, capture_output=True, text=True, check=True, timeout=60)
        # Persist the normal host launcher command across updates. Bootstrap must
        # select the new generation at each start, not a test-selected venv.
        launch_command = json.loads(probe.stdout.strip().splitlines()[-1])
        assert Path(launch_command[0]).is_file(), probe.stdout
        def read_line(child):
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=60):
                    raise TimeoutError('PM lifecycle child did not respond within 60s')
            return child.stdout.readline()
        def start():
            child = subprocess.Popen(launch_command, env=env, cwd=root,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            children.append(child)
            while True:
                line = read_line(child)
                if line.strip() == 'READY':
                    return child
                if not line:
                    raise RuntimeError('cold boot failed')
        def status(child):
            child.stdin.write('status\n'); child.stdin.flush()
            return json.loads(read_line(child))
        children = []
        try:
            old = start()
            initial = status(old)
            assert initial['restart_required'] is False, initial
            # Same version, different code must still select a new generation.
            with (plugin / 'src/hermes_kiokuko/__init__.py').open('a') as target:
                target.write('\n# same-version lifecycle fixture\n')
            subprocess.run(['git', '-C', str(plugin), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(plugin), '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '-qm', 'update'], check=True)
            cli('plugins', 'update', 'kiokuko-tools')
            assert status(old)['restart_required'] is True
            fresh = start()
            assert status(fresh)['restart_required'] is False
            selected = status(fresh)['pm_environment']['selected_environment']
            config = (home / 'config.yaml').read_bytes()
            database = home / 'kiokuko/kiokuko.db'
            before_db = database.read_bytes()
            with (plugin / 'pyproject.toml').open('a') as target:
                target.write('\ninvalid TOML !!!\n')
            subprocess.run(['git', '-C', str(plugin), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(plugin), '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '-qm', 'broken'], check=True)
            assert cli('plugins', 'update', 'kiokuko-tools', success=False).returncode != 0
            assert (home / 'config.yaml').read_bytes() == config
            assert database.read_bytes() == before_db
            assert (other / 'config.yaml').read_bytes() == other_config
            assert status(fresh)['pm_environment']['selected_environment'] == selected
            print('PASS: public install/update, committed selection, cold boot, stale process, failure preservation')
        finally:
            for child in children:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()


if __name__ == '__main__':
    main()
