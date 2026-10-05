"""Read-only diagnostics work even when native configuration is invalid."""
import importlib.metadata
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .compatibility import check_host
from .config import read_yaml, load_config
from .errors import KiokukoError
from .filesystem import checked_file
from .store import SCHEMA_VERSION


def diagnose(home, *, running=False):
    home = Path(home).resolve()
    from .runtime import provider_ready
    result = {'python': sys.executable, 'python_version': sys.version.split()[0],
              'profile_path': str(home), 'package_path': str(Path(__file__).parent),
              'version': __version__, 'runtime': 'gateway_loaded' if running else 'cli_process',
              'gateway_loaded': True if running else None,
              'memory_provider_initialized_in_this_process': provider_ready(home), 'schema': SCHEMA_VERSION,
              'delivery_interpretation': 'observed_in_history proves input presence, not answer application',
              'errors': [], 'deliveries': {}, 'candidates': {}, 'operations': {},
              'recent_operations': [], 'recent_deliveries': [], 'sync_skips_and_errors': []}
    try:
        cfg = read_yaml(home / 'config.yaml')
        result['memory_settings'] = {k: cfg.get('memory', {}).get(k) for k in ('provider', 'memory_enabled', 'user_profile_enabled')}
        result['plugin_settings'] = {k: cfg.get('plugins', {}).get(k, []) for k in ('enabled','disabled')}
        check_host(home)
        result['host_ready'] = True
    except (KiokukoError, OSError, AttributeError, TypeError) as error:
        result['host_ready'] = False
        result['errors'].append(getattr(error, 'code', 'DIAGNOSTIC_CONFIG_UNAVAILABLE'))
    result['entrypoints'] = [{ 'group': group, 'name': ep.name, 'value': ep.value}
        for group in ('hermes_agent.plugins', 'hermes_agent.memory_providers')
        for ep in importlib.metadata.entry_points(group=group) if ep.name in {'kiokuko', 'kiokuko-tools'}]
    path = home / 'kiokuko' / 'kiokuko.db'
    try:
        checked_file(path)
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15) as db:
            db.row_factory = sqlite3.Row
            for key, table in [('deliveries','retrieval_deliveries'),('candidates','memory_candidates'),('operations','explicit_operation_receipts')]:
                result[key] = dict(db.execute(f'SELECT state,count(*) FROM {table} GROUP BY state'))
            result['recent_deliveries'] = [dict(r) for r in db.execute('''SELECT d.id,d.state,d.created_at,
                (SELECT count(*) FROM retrieval_delivery_entries WHERE delivery_id=d.id) AS selected_entries
                FROM retrieval_deliveries d ORDER BY created_at DESC LIMIT 10''')]
            result['recent_operations'] = [dict(r) for r in db.execute('SELECT operation,state,entry_id,entry_revision,created_at FROM explicit_operation_receipts ORDER BY created_at DESC LIMIT 20')]
            result['sync_skips_and_errors'] = [dict(r) for r in db.execute('SELECT * FROM status_events ORDER BY updated_at DESC')]
            from contextlib import nullcontext
            from types import SimpleNamespace
            from .monitor import status as monitor_status
            from .operations import verify
            try:
                view = SimpleNamespace(transaction=lambda: nullcontext(db),
                    config=load_config(home), store=SimpleNamespace(directory=path.parent))
                result.update(verify(view))
                result['monitor'] = monitor_status(view)
            except (KiokukoError, OSError, sqlite3.Error):
                result['errors'].append('DIAGNOSTIC_DETAIL_UNAVAILABLE')
            result['store_ready'] = True
    except (KiokukoError, OSError, sqlite3.Error):
        result['store_ready'] = False
        result['errors'].append('DIAGNOSTIC_STORE_UNAVAILABLE')
    return result
