"""Bind a human Gateway event to the host's subsequent raw-argument handler.

No operation runs in the pre-auth hook. Hermes retains authentication, command
policies, hook interception and reply delivery in its normal dispatch pipeline.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import weakref

from .compatibility import active_home, check_host
from .errors import KiokukoError

_pending = ContextVar('kiokuko_gateway_command', default=None)


@dataclass(frozen=True)
class IncomingCommand:
    owner: object
    task: object
    event: object
    gateway: object


class GatewayCommands:
    def __init__(self, ctx, commands):
        self.ctx = ctx
        self.commands = commands
        self.lock = asyncio.Lock()

    def __call__(self, *, event, gateway, **kwargs):
        # Clear on every inbound event, including ordinary chat. The reference
        # grants no authority until the host actually dispatches the command.
        _pending.set(None)
        if getattr(event, 'internal', False) or not getattr(event, 'allow_gateway_control', False):
            return None
        command = (event.get_command() or '').replace('_', '-')
        if command not in self.commands:
            return None
        task = asyncio.current_task()
        if task is not None:
            _pending.set(IncomingCommand(self, weakref.ref(task), event, gateway))
        return None

    async def execute(self, request, command, raw_args):
        try:
            event, gateway = request.event, request.gateway
            source = event.source
            if source is None or getattr(source, 'profile_route_rejected', False) or \
                    getattr(event, 'internal', False) or not event.allow_gateway_control:
                raise KiokukoError('GATEWAY_CONTEXT_UNAVAILABLE')
            if (event.get_command() or '').replace('_', '-') != command or \
                    event.get_command_args().strip() != raw_args.strip():
                raise KiokukoError('GATEWAY_CONTEXT_MISMATCH')
            from agent.delegation_context import is_delegated_child_context
            from tools.skill_provenance import get_current_write_origin
            if is_delegated_child_context() or get_current_write_origin() == 'background_review':
                raise KiokukoError('GATEWAY_CONTEXT_UNAVAILABLE')
            if not gateway._is_user_authorized_for_source(source):
                raise KiokukoError('GATEWAY_ACCESS_DENIED')
            denied = gateway._check_slash_access(source, command)
            if denied is not None:
                return denied
            if getattr(gateway, '_draining', False):
                return 'Hermesを再起動中です。起動後にもう一度実行してください。'
            home = active_home()
            if Path(self.ctx._manager.home_path).resolve() != home:
                raise KiokukoError('PROFILE_IDENTITY_MISMATCH')
            # Serial controls keep enable/disable ordered. Disk/Node work runs
            # off the event loop; replies still go through the host dispatcher.
            async with self.lock:
                return await asyncio.to_thread(self._execute, self.commands[command], home, raw_args)
        except KiokukoError as error:
            return f'コマンドを実行できませんでした ({error.code})。'
        except (OSError, ValueError, AttributeError, ImportError, RuntimeError):
            return 'コマンドを実行できませんでした (GATEWAY_COMMAND_UNAVAILABLE)。'

    @staticmethod
    def _execute(handler, home, args):
        check_host(home)
        return handler.execute(home, args)


def dispatch(ctx, command, raw_args):
    """Consume one host event on its originating task, never an inherited child."""
    request = _pending.get()
    if request is None:
        return None
    _pending.set(None)
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    if request.owner.ctx is not ctx or request.task() is not task:
        return None
    return request.owner.execute(request, command, raw_args)
