"""Verify a built wheel against an isolated install target and Hermes profile.

Usage: .venv/bin/python scripts/verify_research_package.py path/to/wheel
Requires the audited Hermes fixture and uv; performs no provider/model calls.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='kiokuko-package-') as tmp:
        root = Path(tmp)
        installed = root / 'installed'
        subprocess.run(['uv', '--cache-dir', str(root/'cache'), 'pip', 'install',
                        '--no-deps', '--offline', '--target', str(installed), str(wheel)], check=True)
        sys.path.insert(0, str(installed))
        import hermes_kiokuko
        assert Path(hermes_kiokuko.__file__).is_relative_to(installed)
        from hermes_kiokuko.bundled_skills import skill_root
        assert skill_root().is_relative_to(installed)
        assert len(list(skill_root().glob('*/SKILL.md'))) == 6
        from importlib.metadata import entry_points
        for group, name in [('hermes_agent.plugins','kiokuko-tools'), ('hermes_agent.memory_providers','kiokuko')]:
            assert any(ep.name == name for ep in entry_points(group=group))
        home = root / 'profile'
        os.environ['HERMES_HOME'] = str(home)
        os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
        for key in list(os.environ):
            if key.startswith('HERMES_SESSION_') or key.endswith(('_API_KEY','_TOKEN','_SECRET','_PASSWORD')):
                os.environ.pop(key)
        from hermes_kiokuko.config import setup
        from hermes_kiokuko.store import Store
        setup(home); Store(home, initialize=True).close()
        from hermes_cli.plugins import get_plugin_manager, _reset_plugin_managers_for_tests
        manager = get_plugin_manager(); manager.discover_and_load()
        assert manager._plugins['kiokuko-tools'].enabled
        for skill in skill_root().glob('*/SKILL.md'):
            loaded = manager._plugin_skills[f'kiokuko-tools:{skill.parent.name}']['path']
            assert Path(loaded).is_relative_to(installed)
        from tools.skills_tool import skill_view
        assert 'Preserve the current' in str(skill_view('kiokuko-tools:memory-reasoning'))
        from hermes_kiokuko.diagnostics import diagnose
        assert diagnose(home)['host_ready']
        _reset_plugin_managers_for_tests()
        print('PASS: wheel isolated install, entrypoints, 6 read-only Skills and unchanged host discovery')


if __name__ == '__main__':
    main()
