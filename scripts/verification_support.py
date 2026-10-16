"""Current-host API checks and artifacts from one fresh build.

Checksum identity is opt-in for historical fixture reproduction, never the
default compatibility gate. No production Hermes profile is consulted.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HOST_PROBE = '''
import importlib, inspect, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
checks = []
contracts = [
    ('pm.environment', 'PythonEnvironment', {'uv','python','destination','cache','env','output','no_config'}),
    ('pm.workspace', 'lock_and_sync', {'root','source','seed_lock','environment'}),
    ('hermes_cli._launchers', 'runtime_command', {'code'}),
    ('hermes_cli.plugins', 'get_plugin_manager', set()),
]
for module, attribute, required in contracts:
    check = {'name': module + '.' + attribute, 'ok': False}
    try:
        imported = importlib.import_module(module)
        if not pathlib.Path(imported.__file__).resolve().is_relative_to(root):
            raise ImportError('foreign host module')
        value = getattr(imported, attribute)
        parameters = inspect.signature(value).parameters
        keywords = {n for n,p in parameters.items() if p.kind in (p.POSITIONAL_OR_KEYWORD,p.KEYWORD_ONLY)}
        check['ok'] = callable(value) and (required <= keywords or any(p.kind == p.VAR_KEYWORD for p in parameters.values()))
    except Exception as error:
        check['reason'] = type(error).__name__
    checks.append(check)
checks.append({'name':'hermes_cli/main.py','ok':(root/'hermes_cli/main.py').is_file()})
print(json.dumps({'ok':all(c['ok'] for c in checks),'checks':checks}))
'''


def verify_host(host, fixture=None):
    host = Path(host).resolve()
    if fixture is not None:
        pin = json.loads((ROOT / 'tests/hermes_e2e/pin.json').read_text())['fixtures'][fixture]
        for name, expected in pin['files'].items():
            if hashlib.sha256((host / name).read_bytes()).hexdigest() != expected:
                raise ValueError('Historical fixture source mismatch: ' + name)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'PYTHON'))
           and not k.endswith(('_TOKEN', '_SECRET', '_PASSWORD', '_API_KEY'))}
    with tempfile.TemporaryDirectory(prefix='kiokuko-host-check-') as temporary:
        env.update(HERMES_HOME=temporary, HERMES_DISABLE_LAZY_INSTALLS='1', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-I', '-c', HOST_PROBE, str(host)],
                                env=env, cwd=temporary, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('Host API probe failed')
    report = json.loads(result.stdout.splitlines()[-1])
    if not report['ok']:
        raise ValueError('Host API mismatch: ' + ', '.join(c['name'] for c in report['checks'] if not c['ok']))
    return {**report, 'historical_fixture': fixture}


def build_artifacts(output_root):
    """Return a manifest for exactly this build, never a glob of old dist files."""
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='verification-build-', dir=output_root))
    subprocess.run([sys.executable, '-m', 'build', '--outdir', str(directory)],
                   cwd=ROOT, stdout=sys.stderr, check=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    manifest = {'version': version, 'sha256': {}}
    for kind, pattern in [('wheel', '*.whl'), ('sdist', '*.tar.gz')]:
        files = list(directory.glob(pattern))
        if len(files) != 1:
            raise ValueError('Expected one current-build ' + kind)
        artifact = files[0]
        if kind == 'wheel':
            with zipfile.ZipFile(artifact) as archive:
                entries = [p for p in archive.namelist() if p.endswith('.dist-info/METADATA')]
                if len(entries) != 1:
                    raise ValueError('Invalid wheel metadata')
                metadata = archive.read(entries[0])
        else:
            with tarfile.open(artifact) as archive:
                entries = [p for p in archive.getmembers() if p.name.count('/') == 1 and p.name.endswith('/PKG-INFO')]
                if len(entries) != 1:
                    raise ValueError('Invalid sdist metadata')
                metadata = archive.extractfile(entries[0]).read()
        parsed = BytesParser().parsebytes(metadata)
        if parsed['Name'] != 'hermes-kiokuko' or parsed['Version'] != version:
            raise ValueError('Artifact does not match current project metadata')
        manifest[kind] = str(artifact)
        manifest['sha256'][kind] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path = directory / 'artifacts.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    return path


def artifact_path(manifest_path, kind):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    artifact = Path(manifest[kind]).resolve()
    if artifact.parent != manifest_path.parent or hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest['sha256'][kind]:
        raise ValueError('Artifact location or digest changed')
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build')
    build.add_argument('--output-root', type=Path, default=ROOT / '.cache')
    artifact = commands.add_parser('artifact')
    artifact.add_argument('manifest', type=Path)
    artifact.add_argument('kind', choices=['wheel', 'sdist'])
    args = parser.parse_args()
    print(build_artifacts(args.output_root) if args.command == 'build' else artifact_path(args.manifest, args.kind))


if __name__ == '__main__':
    main()
