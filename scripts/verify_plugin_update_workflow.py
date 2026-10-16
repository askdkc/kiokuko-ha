"""Exercise public Hermes install/list/update with local Git transport fixtures.

Only the GitHub transport is redirected to owned temporary repositories. Hermes'
CLI parser, profile selection, installer, scanner, listing and updater are real.
PM may download its own runtime/tools. No live profile or repository is modified.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://github.com/askdkc/kiokuko-ha.git'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    git = shutil.which('git')
    if not git or not (host / 'hermes_cli/main.py').is_file():
        parser.error('an existing Hermes source checkout and git are required')
    records = []
    with tempfile.TemporaryDirectory(prefix='plugin-cli-', dir=ROOT / '.cache') as temporary:
        root = Path(temporary)
        copy = root / 'hermes-agent'
        shutil.copytree(host, copy, ignore=shutil.ignore_patterns('__pycache__', '.git', '.venv', 'venv', 'tests', 'docs'))
        (copy / '.venv').symlink_to(Path(sys.prefix), target_is_directory=True)
        account = root / 'account'; account.mkdir()
        home = account / '.hermes'; home.mkdir()
        main_home = home / 'profiles/main'; main_home.mkdir(parents=True)
        for directory in (home, main_home):
            (directory / 'config.yaml').write_text('plugins: {enabled: [], disabled: []}\n')
        # A saved active profile must not redirect explicit --profile default.
        (home / 'active_profile').write_text('main\n')
        repository = root / 'remote'; repository.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'PYTHON', 'GIT_', 'UV_', 'PIP_'))
               and k != 'VIRTUAL_ENV' and not k.endswith(('_TOKEN', '_SECRET', '_PASSWORD', '_API_KEY'))}
        env.update(HOME=str(account), HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(root / 'tools'),
                   HERMES_DISABLE_LAZY_INSTALLS='1', PYTHONPATH=str(copy), UV_CACHE_DIR=str(ROOT / '.cache/uv-native'))
        def fixture_git(*arguments):
            return subprocess.run([git, '-C', str(repository), *arguments], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()
        fixture_git('init', '-q')
        def revision(name, marker):
            (repository / 'plugin.yaml').write_text(f'name: {name}\nversion: "{version}"\ndescription: Download key fixture\n')
            (repository / '__init__.py').write_text('def register(ctx):\n    pass\n')
            (repository / 'revision.txt').write_text(marker)
            fixture_git('add', '.')
            fixture_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', marker)
            return fixture_git('rev-parse', 'HEAD')
        revision('kiokuko-ha', 'old-install')
        # Git itself redirects the public URL. No Hermes function or persisted
        # install record is patched. The stored source/origin remains HTTPS.
        binary = root / 'bin'; binary.mkdir()
        wrapper = binary / 'git'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(git) + ' -c ' +
                           shlex.quote(f'url.{repository.as_uri()}.insteadOf={SOURCE}') + ' "$@"\n')
        wrapper.chmod(0o755)
        launcher = binary / 'hermes'
        launcher.write_text('from hermes_cli.main import main\nraise SystemExit(main())\n')
        env['PATH'] = str(binary) + os.pathsep + env['PATH']
        def cli(profile, *arguments, success=True):
            result = subprocess.run([sys.executable, str(launcher), '--profile', profile, *arguments],
                                    env=env, cwd=root, text=True, capture_output=True, timeout=600)
            records.append({'profile': profile, 'args': arguments, 'returncode': result.returncode,
                            'stdout': result.stdout, 'stderr': result.stderr})
            (output / 'commands.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
            if success and result.returncode:
                raise RuntimeError(f'{arguments}: {result.stderr[-2500:]} {result.stdout[-2500:]}')
            return result
        before_main = (main_home / 'config.yaml').read_bytes()
        assert cli('default', 'plugins', 'update', 'kiokuko-tools', success=False).returncode != 0
        cli('default', 'plugins', 'install', 'askdkc/kiokuko-ha', '--enable', '--yes-deps')
        legacy = home / 'plugins/kiokuko-ha'
        assert legacy.is_dir() and not (main_home / 'plugins/kiokuko-ha').exists()
        assert (main_home / 'config.yaml').read_bytes() == before_main
        rows = json.loads(cli('default', 'plugins', 'list', '--json', '--no-bundled').stdout)
        assert any(r['name'] == 'kiokuko-ha' for r in rows)
        expected = revision('kiokuko-ha', 'source-update')
        cli('default', 'plugins', 'update', legacy.relative_to(home / 'plugins').as_posix())
        assert (legacy / 'revision.txt').read_text() == 'source-update'
        metadata = json.loads((home / 'plugins/.install-metadata.json').read_text())
        assert metadata['kiokuko-ha']['source'] == SOURCE and metadata['kiokuko-ha']['revision'] == expected
        revision('kiokuko-tools', 'renamed-source')
        (legacy / 'user-settings.txt').write_text('fixture-user-setting')
        assert cli('default', 'plugins', 'update', 'kiokuko-ha', success=False).returncode != 0
        assert (legacy / 'revision.txt').read_text() == 'source-update'  # rejected rename preserves old tree
        backup = home / 'kiokuko-plugin-backup'; shutil.copytree(legacy, backup / 'plugin')
        cli('default', 'plugins', 'remove', 'kiokuko-ha')
        cli('default', 'plugins', 'install', 'askdkc/kiokuko-ha', '--enable', '--yes-deps')
        assert (backup / 'plugin/revision.txt').read_text() == 'source-update'
        assert (backup / 'plugin/user-settings.txt').read_text() == 'fixture-user-setting'
        assert (home / 'plugins/kiokuko-tools/revision.txt').read_text() == 'renamed-source'
        rows = json.loads(cli('default', 'plugins', 'list', '--json', '--no-bundled').stdout)
        assert any(r['name'] == 'kiokuko-tools' for r in rows)
        cli('default', 'plugins', 'update', 'kiokuko-tools')
        before_default = (home / 'config.yaml').read_bytes()
        cli('main', 'plugins', 'install', 'askdkc/kiokuko-ha', '--enable', '--yes-deps')
        assert (main_home / 'plugins/kiokuko-tools').is_dir()
        rows = json.loads(cli('main', 'plugins', 'list', '--json', '--no-bundled').stdout)
        assert any(r['name'] == 'kiokuko-tools' for r in rows)
        cli('main', 'plugins', 'update', 'kiokuko-tools')
        assert (home / 'config.yaml').read_bytes() == before_default
        # Existing nested directory keys must be passed whole, not the bare leaf.
        nested = main_home / 'plugins/custom/kiokuko-tools'; nested.parent.mkdir()
        shutil.move(str(main_home / 'plugins/kiokuko-tools'), nested)
        cli('main', 'plugins', 'list', '--no-bundled')
        assert cli('main', 'plugins', 'update', 'kiokuko-tools', success=False).returncode != 0
        # This audited PM accepts a nested lookup key, then refuses nested
        # publication. Preserve that limitation rather than report success.
        assert cli('main', 'plugins', 'update', 'custom/kiokuko-tools', success=False).returncode != 0
        assert (nested / 'revision.txt').read_text() == 'renamed-source'
        local = main_home / 'plugins/local-copy'
        local.mkdir(); (local / 'plugin.yaml').write_text('name: local-copy\n')
        (local / '__init__.py').write_text('def register(ctx):\n    pass\n')
        assert cli('main', 'plugins', 'update', 'local-copy', success=False).returncode != 0
        assert cli('main', 'plugins', 'update', 'web/firecrawl', success=False).returncode != 0
    print('PASS: public-source install/list/exact-directory-key/update; legacy name and rename refusal; backed-up reinstall; default/main isolation; nested publication refusal; absent/local/bundled refusal')


if __name__ == '__main__':
    main()
