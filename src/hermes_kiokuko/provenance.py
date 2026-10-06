"""Read-only process identity; PM selection is not proof of live activation."""
import hashlib
import importlib.metadata
import sys
from pathlib import Path

from . import __version__
from .errors import KiokukoError

_PACKAGE = Path(__file__).resolve().parent
_LOADED_VERSION = __version__


def _fingerprint():
    digest = hashlib.sha256()
    for path in sorted(_PACKAGE.glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


try:
    _LOADED_FINGERPRINT = _fingerprint()
except OSError:
    _LOADED_FINGERPRINT = None


def pm_environment():
    """Inspect the recorded selection without activating, repairing or creating it."""
    result = {'checked': False, 'selected_environment': None, 'active': None}
    try:
        from pm.paths import repo_root
        from pm.environments import committed_venv, site_packages
        selected = committed_venv(repo_root())
        result['checked'] = True
        if selected is not None:
            selected = Path(selected).resolve()
            site = Path(site_packages(selected)).resolve()
            result.update(selected_environment=str(selected), site_packages=str(site),
                          active=any(Path(p).resolve() == site for p in sys.path if p),
                          package_in_generation=_PACKAGE.is_relative_to(selected.parent))
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError, TypeError):
        result['error'] = 'PM_ENVIRONMENT_UNAVAILABLE'
    return result


def runtime_provenance(manifest=None):
    """Report configured, installed and loaded identities independently."""
    distribution = {'available': False}
    try:
        dist = importlib.metadata.distribution('hermes-kiokuko')
        distribution = {'available': True, 'version': dist.version,
                        'location': str(Path(dist.locate_file('')).resolve())}
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        pass
    configured = {key: getattr(manifest, key, None) for key in ('source', 'version', 'path')}
    if configured['path'] is not None:
        configured['path'] = str(configured['path'])
    # A loaded manifest may itself be stale. Read current disk metadata separately.
    if configured['path']:
        try:
            from .config import read_yaml
            configured['disk_version'] = read_yaml(Path(configured['path']) / 'plugin.yaml').get('version')
        except (KiokukoError, OSError, ValueError, TypeError, AttributeError):
            configured['disk_version'] = None
    pm = pm_environment()
    try:
        changed = _LOADED_FINGERPRINT != _fingerprint() if _LOADED_FINGERPRINT else None
    except OSError:
        changed = None
    mismatch = (pm.get('selected_environment') is not None and
                (pm['active'] is False or pm.get('package_in_generation') is False))
    versions_changed = (configured.get('disk_version') is not None and
                        str(configured['disk_version']) != str(configured['version']))
    distribution_changed = distribution.get('available') and distribution.get('version') != _LOADED_VERSION
    restart = True if mismatch or changed or versions_changed or distribution_changed else (False if pm.get('active') is True and changed is False else None)
    return {'observed_in': 'current_process', 'python': sys.executable, 'prefix': sys.prefix,
            'configured_plugin': configured, 'distribution': distribution,
            'loaded': {'version': _LOADED_VERSION, 'package_path': str(_PACKAGE),
                       'origin': ('pm_generation' if pm.get('package_in_generation') is True else
                                  'outside_pm_generation' if pm.get('package_in_generation') is False else 'unknown'),
                       'fingerprint': _LOADED_FINGERPRINT, 'files_changed': changed},
            'pm_environment': pm, 'restart_required': restart}
