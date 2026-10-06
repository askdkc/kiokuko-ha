"""Build the native sdist through pinned current Hermes PM in a disposable home."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SMOKE = r'''
import json, sys
from pathlib import Path
from importlib.metadata import version
from hermes_kiokuko import __version__
from hermes_kiokuko.config import setup
from hermes_kiokuko.compatibility import active_home, check_host
home = active_home()
setup(home)
check_host(home)
from hermes_cli.plugin_validate import validate_plugin_dir
report = validate_plugin_dir(home / 'plugins' / 'kiokuko-tools')
assert report.ok, report.failures
from hermes_cli.plugins import get_plugin_manager
manager = get_plugin_manager()
manager.discover_and_load()
loaded = manager._plugins['kiokuko-tools']
assert loaded.enabled and not loaded.error
assert loaded.manifest.source == 'user', loaded.manifest
assert set(loaded.tools_registered) == {'kiokuko_recall','kiokuko_propose','kiokuko_manage'}
assert 'plugins update kiokuko-tools' in manager._plugin_commands['kiokuko-update']['handler'].execute(home, '')
from plugins.memory import find_provider_dir, load_memory_provider
assert find_provider_dir('kiokuko') is not None
provider = load_memory_provider('kiokuko')
assert provider is not None
provider.initialize('native-install-smoke', hermes_home=str(home))
assert provider.get_tool_schemas() == []  # Publication belongs to the general plugin.
from hermes_kiokuko.config import read_yaml
from hermes_cli.tools_config import _get_platform_tools
from model_tools import get_tool_definitions, handle_function_call
from agent.turn_context import _collect_pre_llm_call_context
from types import SimpleNamespace
expected = {'kiokuko_recall','kiokuko_propose','kiokuko_manage'}
enabled = sorted(_get_platform_tools(read_yaml(home / 'config.yaml'), 'cli'))
catalog = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True)
assert expected <= {tool['function']['name'] for tool in catalog}, enabled
search = json.loads(handle_function_call('tool_search', {'queries':['kiokuko'],'limit':10}, enabled_toolsets=enabled))
assert expected <= set(search['tools']), search
agent = SimpleNamespace(session_id='native-install-smoke', platform='cli', model='test')
_collect_pre_llm_call_context(agent, effective_task_id='smoke', turn_id='turn',
    original_user_message='Use Japanese replies', messages=[{'role':'user','content':'Use Japanese replies'}], conversation_history=[])
def call(name, args):
    return json.loads(handle_function_call(name, args, session_id=agent.session_id,
        task_id='smoke', turn_id='turn', enabled_toolsets=enabled))
proposal = call('kiokuko_propose', {'action':'propose','claim':'Use Japanese replies','scope':'principal'})
assert proposal['ok'] and proposal['data']['state'] == 'pending', proposal
from hermes_kiokuko import runtime
with runtime.current().transaction() as sql:
    assert sql.execute('SELECT count(*) FROM memory_entries').fetchone()[0] == 0
from hermes_kiokuko.cli import execute, setup_parser
import argparse
parser = argparse.ArgumentParser()
setup_parser(parser)
proposal_id = proposal['data']['id']
approved = execute(parser.parse_args(['approve',proposal_id]), home, input_fn=lambda _: proposal_id, output=lambda _: None)
with runtime.current().transaction() as sql:
    assert tuple(sql.execute('SELECT state,promoted_entry_id FROM memory_candidates WHERE id=?',(proposal_id,)).fetchone()) == ('accepted',approved['entry_id'])
recalled = call('kiokuko_recall', {'action':'get','entry_id':approved['entry_id']})
assert recalled['ok'] and recalled['data']['id'] == approved['entry_id']
assert recalled['data']['confirmation_kind'] == 'cli_approved'
provider.shutdown()
from hermes_cli.plugin_dev import doctor_plugin
report = doctor_plugin(home / 'plugins' / 'kiokuko-tools')
assert report.ok, report.findings
assert version('hermes-kiokuko') == __version__
import hermes_kiokuko
assert Path(hermes_kiokuko.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
print('PASS: current PM native install; normal setup catalog/search/canonical proposal, human approval and persisted recall; provider lifecycle, doctor and managed update')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--uv', type=Path, default=shutil.which('uv'))
    args = parser.parse_args()
    host = args.host.resolve()
    pin = json.loads((ROOT / 'tests/hermes_e2e/pin.json').read_text())['fixtures']['current-88c6085']
    for name, digest in pin['files'].items():
        assert hashlib.sha256((host / name).read_bytes()).hexdigest() == digest, name
    if not args.uv or not args.uv.is_file():
        parser.error('an installed uv executable is required')
    sys.path.insert(0, str(host))
    from pm.environment import PythonEnvironment
    from pm.workspace import lock_and_sync
    with tempfile.TemporaryDirectory(prefix='native-plugin-', dir=ROOT / '.cache') as directory:
        root = Path(directory)
        home = root / 'home'
        unpacked = root / 'unpacked'
        with tarfile.open(args.archive) as source:
            source.extractall(unpacked, filter='data')
        sources = list(unpacked.iterdir())
        assert len(sources) == 1 and sources[0].is_dir()
        plugin = home / 'plugins' / 'kiokuko-tools'
        shutil.copytree(sources[0], plugin)
        generation = root / 'generation'
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('HERMES_', 'PYTHON', 'UV_', 'PIP_'))
               and not key.endswith(('_TOKEN', '_SECRET', '_PASSWORD', '_API_KEY'))}
        env.update(HERMES_HOME=str(home), HERMES_DISABLE_LAZY_INSTALLS='1')
        environment = PythonEnvironment(uv=args.uv.resolve(), python=Path(sys.executable),
            destination=generation / '.venv', cache=ROOT / '.cache/uv-native',
            env=env, output=sys.stderr, no_config=True)
        lock_and_sync([plugin], [], root=generation, source=host,
                      seed_lock=host / 'uv.lock', environment=environment)
        environment.check()
        env['PYTHONPATH'] = str(generation)
        subprocess.run([str(environment.executable), '-c', SMOKE, str(generation)],
                       env=env, cwd=root, check=True, timeout=180)


if __name__ == '__main__':
    main()
