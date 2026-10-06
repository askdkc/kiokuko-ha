"""Hermes directory-plugin entry point; PM installs the sibling Python project."""


def register(ctx):
    try:
        from hermes_kiokuko.plugin_entry import register as register_tools
        register_tools(ctx, managed=True)
    except ModuleNotFoundError as error:
        import json
        import logging
        import os
        import sys
        from pathlib import Path
        # Only allowlisted process facts; no config values or arbitrary exception text.
        facts = {'code': 'KIOKUKO_PACKAGE_NOT_IMPORTABLE' if error.name == 'hermes_kiokuko'
                 else 'KIOKUKO_DEPENDENCY_NOT_IMPORTABLE',
                 'python': sys.executable, 'python_version': sys.version.split()[0], 'prefix': sys.prefix,
                 'profile_path': os.environ.get('HERMES_HOME'),
                 'plugin_path': str(Path(__file__).resolve().parent),
                 'search_paths': [p for p in sys.path if p and
                                  ('site-packages' in p or 'plugin-sources' in p)],
                 'remedy': 'Check target profile PM selection and Gateway startup; restart after repair.'}
        logging.getLogger('hermes_cli.plugins').error('Kiokuko import diagnostic: %s', json.dumps(facts))
        raise
