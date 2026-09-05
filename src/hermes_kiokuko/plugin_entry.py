from .compatibility import surface_is_compatible_and_selected
from .plugin_tools import recall_handler, propose_handler, manage_handler
from .schemas import RECALL_SCHEMA, PROPOSE_SCHEMA, MANAGE_SCHEMA
from .tool_context import tool_execution_middleware
from .turn_hook import pre_llm_call


def register(ctx):
    from .cli import setup_parser, cli_handler
    for name, schema, handler in (
            ("kiokuko_recall", RECALL_SCHEMA, recall_handler),
            ("kiokuko_propose", PROPOSE_SCHEMA, propose_handler),
            ("kiokuko_manage", MANAGE_SCHEMA, manage_handler)):
        ctx.register_tool(name=name, toolset="memory", schema=schema, handler=handler,
                          check_fn=surface_is_compatible_and_selected,
                          description="Kiokuko scoped memory; model proposals require human approval")
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_middleware("tool_execution", tool_execution_middleware)
    ctx.register_cli_command("kiokuko", "Kiokuko memory management", setup_parser, cli_handler)
    from .curation import setup_parser as curation_parser
    ctx.register_cli_command("kioku-curation", "検証済みのプロジェクト記憶を選んでGlobalへ共有", curation_parser, cli_handler)
    from .slash_curation import SlashCuration
    ctx.register_command("kioku-curation", SlashCuration(ctx),
                         description="検証済み記憶の選択・Global共有（対話CLI）",
                         args_hint="[show|select 1 3|all|none|share|confirm CODE|cancel|help]")
    from .slash_update import SlashUpdate
    ctx.register_command("kiokuko-update", SlashUpdate(ctx),
                         description="現在のHermes用Python環境のKiokukoを更新（対話CLI）",
                         args_hint="[status|retry|help]")
