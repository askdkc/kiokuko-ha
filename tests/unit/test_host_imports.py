"""Exercise stale editable mappings from a foreign working directory."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = r'''
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import sys

host, kiokuko, mode = sys.argv[1:]
# Match the old setuptools editable finder: installed packages are known,
# but a root module added by a later checkout update is not in its mapping.
sys.meta_path = [finder for finder in sys.meta_path
                 if 'editable' not in str(finder).lower()]
sys.path = [p for p in sys.path if '/.cache/hermes' not in p]
sys.path.insert(0, kiokuko)
class InstalledHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'hermes_cli', 'hermes_constants'}:
            return importlib.machinery.PathFinder.find_spec(fullname, [host])
sys.meta_path.insert(0, InstalledHost())
try:
    import hermes_cli.plugins
except ModuleNotFoundError as error:
    assert error.name == 'hermes_yaml'
else:
    raise AssertionError('precondition: the stale mapping must fail')
from hermes_kiokuko.compatibility import active_home, host_contract_report
if mode == 'home':
    active_home()
report = host_contract_report()
check = next(c for c in report['checks'] if c['name'] == 'import:hermes_cli.plugins')
print(json.dumps({'check': check, 'host_path_added': host in sys.path,
                  'host_path_count': sys.path.count(host),
                  'yaml_loaded': 'hermes_yaml' in sys.modules}))
'''


@pytest.fixture
def source_host(tmp_path):
    root = (tmp_path / 'installed-host').resolve()
    package = root / 'hermes_cli'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('__version__ = "0.21.99"\n')
    (package / 'plugins.py').write_text('import hermes_yaml\n')
    (root / 'hermes_constants.py').write_text(
        'import hermes_yaml\nfrom pathlib import Path\ndef get_hermes_home(): return Path("profile")\n')
    (root / 'pyproject.toml').write_text('[project]\nname = "hermes-agent"\n')
    (root / 'hermes_yaml.py').write_text('import yaml\nassert yaml.safe_load("enabled: true")["enabled"] is True\n')
    return root


def probe(source_host, tmp_path, mode='report'):
    cwd = tmp_path / 'foreign-cwd'
    cwd.mkdir(exist_ok=True)
    # A project-local lookalike must never become the recovery source.
    (cwd / 'hermes_yaml.py').write_text('raise RuntimeError("wrong source")\n')
    import hermes_kiokuko
    source = Path(hermes_kiokuko.__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, '-I', '-c', PROBE,
                             str(source_host), str(source), mode],
                            cwd=cwd, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode', ['report', 'home'])
def test_updated_host_root_module_resolves_from_installed_package(source_host, tmp_path, mode):
    result = probe(source_host, tmp_path, mode)
    assert result['check']['ok']
    assert result['host_path_added'] and result['yaml_loaded']
    assert result['host_path_count'] == 1


def test_missing_host_source_stays_missing(source_host, tmp_path):
    (source_host / 'hermes_yaml.py').unlink()
    result = probe(source_host, tmp_path)
    assert not result['check']['ok']
    assert result['check']['missing_module'] == 'hermes_yaml'
    assert not result['host_path_added'] and not result['yaml_loaded']


def test_missing_yaml_dependency_stays_visible(source_host, tmp_path):
    (source_host / 'hermes_yaml.py').write_text('import missing_yaml_dependency\n')
    result = probe(source_host, tmp_path)
    assert not result['check']['ok']
    assert result['check']['missing_module'] == 'missing_yaml_dependency'
    assert not result['yaml_loaded']


@pytest.mark.parametrize('manifest', ['[project]\nname = "other-project"\n', 'invalid toml'])
def test_unidentified_host_root_is_not_added(source_host, tmp_path, manifest):
    (source_host / 'pyproject.toml').write_text(manifest)
    result = probe(source_host, tmp_path)
    assert not result['check']['ok']
    assert not result['host_path_added'] and not result['yaml_loaded']
