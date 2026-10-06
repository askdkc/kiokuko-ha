"""Memory-only host configuration projection without credential probes or plugin loading."""
import ast

from .tool_context import TOOL_NAMES


def memory_selection(config, platform):
    """Use host static membership/defaults and preserve explicit memory suppression.

    The full host resolver also probes unrelated credentials. This projection
    covers only the plugin's memory toolset; an actual catalog is a separate check.
    """
    from hermes_cli.tools_config import _platform_default_toolset
    from agent.skill_utils import parse_config_string_list
    from toolsets import resolve_toolset, bundle_non_core_tools
    raw = (config.get('platform_toolsets') or {}).get(platform)
    selection = raw
    if isinstance(raw, str) and raw.strip().startswith('['):
        try:
            selection = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            selection = None
    saved = isinstance(selection, list)
    if not saved:
        selection = [_platform_default_toolset(platform)]
    selection = [str(name) for name in selection]
    if 'hermes' in selection:
        selection = [name for name in selection if name != 'hermes'] + ['hermes-cli', 'hermes-api-server']
    requested = set().union(*(set(resolve_toolset(name, include_registry=False)) for name in selection))
    known = set((config.get('known_plugin_toolsets') or {}).get(platform) or ())
    known |= set((config.get('known_builtin_toolsets') or {}).get(platform) or ())
    # memory is a built-in category with plugin additions. Empty selections and
    # a previously offered category omitted from a saved list express opt-out.
    selected = 'memory' in requested or bool(selection) and 'memory' not in known
    tools = set(TOOL_NAMES) if selected else set()
    disabled = parse_config_string_list((config.get('agent') or {}).get('disabled_toolsets'))
    for name in disabled:
        removed = bundle_non_core_tools(name) if name.startswith('hermes-') else resolve_toolset(name)
        if name == 'memory':
            removed = set(removed) | TOOL_NAMES
        tools.difference_update(removed)
    return {'checked': True, 'platform': platform, 'scope': 'memory_only_config_projection',
            'selection_source': 'saved' if saved else 'host_default',
            'selected_toolsets': selection, 'disabled_toolsets': disabled,
            'tools': {name: name in tools for name in sorted(TOOL_NAMES)},
            'available': TOOL_NAMES <= tools}
