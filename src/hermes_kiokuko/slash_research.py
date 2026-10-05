"""Authenticated entry points, scoped memory and durable exact-event replay."""
import json
from pathlib import Path

from .compatibility import active_home, check_host
from .config import load_config
from .errors import KiokukoError
from .filesystem import atomic_write, file_lock, private_directory, acquire_lock
from .identity import opaque
from .models import Identity, canonical, digest, new_id
from .service import Service
from .store import Store


class SlashResearch:
    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, raw_args):
        from .gateway_commands import dispatch
        routed = dispatch(self.ctx, 'kiokuko-research', raw_args)
        if routed is not None:
            return routed
        try:
            from .slash_curation import cli_binding
            from .identity import resolve_identity
            home, session = cli_binding(self.ctx)
            store = Store(home)
            try:
                who = resolve_identity(store, session, 'cli')
            finally:
                store.close()
            return self.execute_request(home, raw_args, who, new_id('research'), session)
        except (KiokukoError, ImportError, OSError) as error:
            return f'調査を実行できません ({getattr(error, "code", "RESEARCH_UNAVAILABLE")})。'

    def execute_gateway(self, home, raw_args, event, cancelled=None):
        if raw_args.strip() == 'status':
            from .diagnostics import diagnose
            return json.dumps(diagnose(home, running=True), ensure_ascii=False, indent=2)
        check_host(home)
        source = event.source
        store = Store(home)
        try:
            platform = getattr(source.platform, 'value', source.platform)
            kind = 'user_id_alt' if getattr(source, 'user_id_alt', None) else 'user_id'
            user = getattr(source, 'user_id_alt', None) or source.user_id
            if not user or not source.chat_id or not event.message_id:
                raise KiokukoError('GATEWAY_CONTEXT_UNAVAILABLE')
            principal = opaque(store.key, 'principal', platform, kind, user)
            conversation = opaque(store.key, 'conversation', platform, source.chat_id, source.thread_id or '')
            dm = source.chat_type in {'dm','private','direct'}
            who = Identity(platform, 'dm' if dm else 'group_chat', principal, conversation, None, 'dm' if dm else 'group')
            event_id = opaque(store.key, 'research', platform, user, source.chat_id, source.thread_id or '', event.message_id)
        finally:
            store.close()
        return self.execute_request(home, raw_args, who, event_id, conversation, cancelled=cancelled)

    def execute_request(self, home, request, who, event_id, session, cancelled=None):
        if isinstance(request, str) and request.strip() == 'status':
            from .diagnostics import diagnose
            return json.dumps(diagnose(home, running=cancelled is not None), ensure_ascii=False, indent=2)
        if not isinstance(request, str) or not request.strip() or len(request) > 2000:
            return '/kiokuko-research <調査したい内容（2000文字以内）>'
        from .capture_requests import HUMAN_ORIGINS
        if who.origin not in HUMAN_ORIGINS or not who.principal_id:
            raise KiokukoError('RESEARCH_HUMAN_REQUIRED')
        check_host(home)
        if active_home() != Path(home).resolve():
            raise KiokukoError('PROFILE_IDENTITY_MISMATCH')
        if load_config(home)['research']['mode'] == 'off':
            return '調査機能は無効です。'
        if cancelled is None:
            import threading
            cancelled = threading.Event()
        directory = home / 'kiokuko' / 'research'
        private_directory(directory)
        path = directory / (digest(event_id) + '.json')
        binding = digest(canonical([request, who.__dict__, session]))
        with file_lock(directory / (digest(event_id) + '.lock'), exclusive=True):
            if path.is_symlink():
                raise KiokukoError('UNSAFE_PATH')
            if path.exists():
                receipt = json.loads(path.read_text())
                if receipt['binding'] != binding:
                    return '同じイベントの内容が変わったため調査を実行できません (RESEARCH_EVENT_CONFLICT)。'
                return receipt.get('reply') or '前回の調査が中断されました。新しいメッセージで依頼してください。'
            # Claim before any paid call. Crashes remain terminal for this event.
            atomic_write(path, canonical({'binding': binding, 'state':'running'}).encode())
            store = Store(home)
            reply, memory_count = '未確認: 調査を完了できませんでした。', 0
            failed = False
            try:
                service = Service(store, host_guard=check_host)
                snap = service.snapshot('research-' + digest(session)[:32], event_id, request, who)
                memories = [{'id': e['id'], 'text': e['claim']} for e in service.search(snap, request)[:8]]
                memory_count = len(memories)
                from .model_job import ModelJob
                from .research import run
                import os
                job = ModelJob(acquire_lock(directory / 'worker.lock', exclusive=True, timeout=0))
                try:
                    reply = job.call(lambda: run(home, request, memories, cancelled=cancelled), timeout=50)
                finally:
                    if not job.detached:
                        os.close(job.fd)
            except Exception as error:
                failed = True
                cancelled.set()
                # Never emit the model candidate or fall back to a normal answer.
                reply = f'未確認: 調査を完了できませんでした ({getattr(error, "code", "RESEARCH_UNAVAILABLE")})。'
            finally:
                store.close()
            if cancelled.is_set() and not failed:
                return '未確認: 調査はキャンセルされました。'
            atomic_write(path, canonical({'binding':binding, 'state':'complete', 'reply':reply,
                'memory_entries_selected':memory_count,
                'ordinary_history_observation':False}).encode())
            return reply
