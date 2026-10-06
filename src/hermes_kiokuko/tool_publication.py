"""Read-only tool visibility, with configured projections separate from live agents."""
import json
import os
import sys
from pathlib import Path

from .compatibility import active_home, surface_selection_report
from .config import read_yaml
from .runtime import provider_ready
from .tool_context import TOOL_NAMES


def loaded_manager(home):
    """Observe only existing managers. Never create one or discover plugins."""
    module = sys.modules.get('hermes_cli.plugins')
    if module is None:
        return None
    managers = list(getattr(module, '_plugin_managers_by_home', {}).values())
    managers.append(getattr(module, '_plugin_manager', None))
    for manager in managers:
        if manager is not None and Path(manager.home_path).resolve() == home and manager._discovered:
            return manager
    return None


def configuration_publication(home, platform):
    """Resolve the host defaults, saved selection and global suppression unchanged."""
    result = {'checked': False, 'platform': platform, 'available': None,
              'tools': {name: None for name in sorted(TOOL_NAMES)}}
    try:
        cfg = read_yaml(home / 'config.yaml')
        from .tool_selection import memory_selection
        result.update(memory_selection(cfg, platform))
        if not result['available']:
            result['error'] = 'MEMORY_TOOLSET_DISABLED'
            result['remedy'] = (f'Run hermes kiokuko setup --enable-memory-toolset {platform} to opt in. '
                                'Global agent.disabled_toolsets suppression must be reviewed separately. '
                                'Restart the agent/Gateway and start a new session; repeating search cannot expose excluded tools.')
    except Exception:
        # Host configuration exceptions can include private settings.
        result.update(available=False, error='TOOLSET_RESOLUTION_UNAVAILABLE')
    return result


def session_publication(agent, home):
    """Inspect an actual in-process agent; never infer it from a fresh doctor process."""
    result = {'status': 'unconfirmed', 'catalog_checked': False, 'search_checked': False,
              'note': '実セッション未確認。新しいdoctorプロセスの成功は既存チャットの成功を証明しない。'}
    if agent is None:
        return result
    # Bind to the provider owned by this agent, not another agent in this process.
    providers = getattr(getattr(agent, '_memory_manager', None), '_providers', ())
    owned = [p for p in providers if getattr(p, 'name', None) == 'kiokuko'
             and getattr(p, '_service', None) is not None
             and p._service.store.home == home]
    if not owned:
        result['error'] = 'SESSION_PROVIDER_PROFILE_MISMATCH'
        return result
    try:
        from model_tools import get_tool_definitions
        from tools.tool_search import dispatch_tool_search
        from .identity import resolve_identity
        identity = resolve_identity(owned[0]._service.store, agent.session_id, agent.platform,
                                    workspace=False, host_session=True)
        if identity.principal_id is None:
            result['error'] = 'SESSION_PRINCIPAL_UNAVAILABLE'
            return result
        visible = {t['function']['name'] for t in agent.tools or []}
        definitions = get_tool_definitions(enabled_toolsets=agent.enabled_toolsets,
            disabled_toolsets=agent.disabled_toolsets, quiet_mode=True, skip_tool_search_assembly=True)
        search = json.loads(dispatch_tool_search({'queries': ['kiokuko'], 'limit': 10},
                                                current_tool_defs=definitions))
        searchable = set(search['tools'])
        bridge = {'tool_search', 'tool_call'} <= visible
        # Catalog reconstruction proves the current bridge scope; direct tools
        # must also occur in the actual agent's frozen public schema snapshot.
        names = {t['function']['name'] for t in definitions}
        from tools.registry import registry
        admission = {}
        for name in sorted(TOOL_NAMES):
            entry = registry.get_entry(name)
            admission[name] = bool(entry and (entry.check_fn is None or entry.check_fn()))
        tools = {name: admission[name] and name in names and (name in visible or bridge and name in searchable)
                 for name in sorted(TOOL_NAMES)}
        result.update(status='observed_in_this_process', catalog_checked=True, search_checked=True,
                      session_id=agent.session_id, platform=agent.platform,
                      principal_bound=True, tools=tools,
                      searchable={name: name in searchable for name in sorted(TOOL_NAMES)},
                      available=all(tools.values()), bridge_active=bridge,
                      snapshot_tools={name: name in visible for name in sorted(TOOL_NAMES)},
                      current_admission=admission,
                      enabled_toolsets=agent.enabled_toolsets, disabled_toolsets=agent.disabled_toolsets,
                      note='Read-only catalog/search observation; tool execution is verified separately.')
    except Exception:
        result['error'] = 'SESSION_CATALOG_UNAVAILABLE'
    return result


def tool_publication(home, *, platform='cli', agent=None, probe_catalog=False):
    home = Path(home).resolve()
    result = {'pid': os.getpid(), 'profile_path': str(home), 'platform': platform,
              'plugin': {'loaded': None, 'scope': 'this_process'},
              'registration': {name: None for name in sorted(TOOL_NAMES)},
              'check_fn': {name: {'allowed': None} for name in sorted(TOOL_NAMES)},
              'provider': {'initialized': provider_ready(home), 'scope': 'this_process'},
              'session': {'status': 'unconfirmed'}, 'errors': []}
    try:
        if active_home() != home:
            raise ValueError('profile mismatch')
        result['provider']['admission'] = surface_selection_report()
        manager = loaded_manager(home)
        loaded = manager._plugins.get('kiokuko-tools') if manager else None
        result['plugin'].update(loaded=bool(loaded and loaded.enabled and not loaded.error),
                                error='PLUGIN_NOT_LOADED_IN_THIS_PROCESS' if not loaded else
                                'GENERAL_PLUGIN_DISABLED' if not loaded.enabled else
                                'PLUGIN_LOAD_FAILED' if loaded.error else None)
        if result['plugin']['error']:
            result['errors'].append(result['plugin']['error'])
        if manager is not None:
            from tools.registry import registry
            for name in sorted(TOOL_NAMES):
                entry = registry.get_entry(name)
                registered = bool(loaded and name in loaded.tools_registered and entry is not None)
                result['registration'][name] = registered
                reason = surface_selection_report(name)
                allowed = bool(registered and (entry.check_fn is None or entry.check_fn()))
                result['check_fn'][name] = {'allowed': allowed, 'error': None if allowed else
                    reason['error'] or ('CHECK_FN_DENIED' if registered else 'CANONICAL_TOOL_NOT_REGISTERED')}
                if not registered:
                    result['errors'].append('CANONICAL_TOOL_NOT_REGISTERED')
                elif not allowed:
                    result['errors'].append(reason['error'] or 'CHECK_FN_DENIED')
        result['configuration'] = configuration_publication(home, platform)
        if result['configuration'].get('error'):
            result['errors'].append(result['configuration']['error'])
        if probe_catalog and manager is not None:
            from hermes_cli.tools_config import _get_platform_tools
            from model_tools import get_tool_definitions
            from tools.tool_search import dispatch_tool_search
            cfg = result['configuration']
            enabled = sorted(_get_platform_tools(read_yaml(home / 'config.yaml'), platform))
            result['configuration']['enabled_toolsets'] = enabled
            defs = get_tool_definitions(enabled_toolsets=enabled,
                disabled_toolsets=cfg.get('disabled_toolsets'), quiet_mode=True, skip_tool_search_assembly=True)
            search = json.loads(dispatch_tool_search({'queries': ['kiokuko'], 'limit': 10}, current_tool_defs=defs))
            result['probe_catalog'] = {name: name in search['tools'] for name in sorted(TOOL_NAMES)}
            result['probe_scope'] = 'fresh_projection_in_doctor_process_not_existing_chat'
            if not all(result['probe_catalog'].values()):
                result['errors'].append('CATALOG_TOOLS_UNAVAILABLE')
        result['session'] = session_publication(agent, home)
        if agent is not None and not result['session'].get('available'):
            result['errors'].append(result['session'].get('error', 'SESSION_TOOLS_UNAVAILABLE'))
        if agent is not None and result['configuration'].get('checked'):
            session_enabled = result['session'].get('enabled_toolsets')
            from toolsets import resolve_toolset
            requested = set().union(*(set(resolve_toolset(name)) for name in session_enabled or ()))
            for name in result['session'].get('disabled_toolsets') or ():
                requested.difference_update(resolve_toolset(name))
            desired = {name for name, enabled in result['configuration']['tools'].items() if enabled}
            result['session']['memory_selection_matches_configuration'] = requested & TOOL_NAMES == desired
        result['restart_or_new_session_required'] = bool(result['errors']) or (
            agent is not None and result['session'].get('memory_selection_matches_configuration') is False)
    except Exception:
        result['configuration'] = {'checked': False, 'available': None}
        result['session'] = session_publication(None, home)
        result['errors'].append('DIAGNOSTIC_TOOL_PROFILE_OR_API_UNAVAILABLE')
    result['errors'] = sorted(set(result['errors']))
    return result
