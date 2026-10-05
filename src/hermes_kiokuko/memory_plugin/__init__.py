"""Package entry point for Hermes's directory-based provider discovery.

The migration check resolves only package directories. Keep registration in the
existing module and use an absolute import: Hermes also loads this package under
its synthetic user-plugin namespace.
"""
from hermes_kiokuko.provider_entry import register
