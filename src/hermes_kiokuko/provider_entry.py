def register(ctx):
    from .provider import KiokukoMemoryProvider
    ctx.register_memory_provider(KiokukoMemoryProvider())
