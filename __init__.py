"""Hermes directory-plugin entry point; PM installs the sibling Python project."""


def register(ctx):
    from hermes_kiokuko.plugin_entry import register as register_tools
    register_tools(ctx, managed=True)
