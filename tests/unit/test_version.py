"""Release metadata and the version captured by running code must agree."""
from pathlib import Path
import tomllib

import yaml

from hermes_kiokuko import __version__


def test_runtime_and_plugin_versions_match_release():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    manifest = yaml.safe_load((root / 'src/hermes_kiokuko/plugin.yaml').read_text())
    release = project['project']['version']
    assert __version__ == release, 'Runtime version would misreport the loaded release'
    assert str(manifest['version']) == release, 'Plugin manifest version is stale'
