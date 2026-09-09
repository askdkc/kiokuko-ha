"""Interactive CLI controls for the current profile's monitor."""
from types import SimpleNamespace

from .compatibility import check_host
from .errors import KiokukoError
from .monitor_cli import execute
from .service import Service
from .slash_curation import cli_binding
from .store import Store

HELP = ("/kiokuko-monitor         状態確認\n"
        "/kiokuko-monitor enable  有効化\n"
        "/kiokuko-monitor disable 停止")


def render(info, profile):
    lines = [f"監視・自動抽出: {'有効' if info['enabled'] else '無効'}（{profile}）"]
    runs, jobs = info['runs'], info['jobs']
    lines.append(f"記録: 完了 {runs.get('complete', 0)}件 / 不完全 {runs.get('incomplete', 0)}件")
    labels = {'done': '完了', 'pending': '待機', 'running': '実行中', 'failed': '失敗', 'blocked': '処理不可'}
    lines.append('抽出: ' + (' / '.join(f'{label} {jobs[state]}件' for state, label in labels.items()
                                      if jobs.get(state)) or '未実行'))
    if info['enabled'] and info['node'] != 'ready':
        lines.append(f"記録用Nodeを利用できません ({info['node']})。")
    action = 'disable' if info['enabled'] else 'enable'
    lines.append(f"{'停止' if info['enabled'] else '有効化'}: /kiokuko-monitor {action}")
    return '\n'.join(lines)


class SlashMonitor:
    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, raw_args):
        try:
            home, session = cli_binding(self.ctx)
            from agent.delegation_context import is_delegated_child_context
            from tools.skill_provenance import get_current_write_origin
            from .identity import bound_values
            bound = bound_values()
            if is_delegated_child_context() or get_current_write_origin() == 'background_review' or \
                    (bound.get('ID') and bound['ID'] != session):
                raise KiokukoError('LOCAL_CLI_REQUIRED')
            action = raw_args.strip() or 'status'
            if action not in {'enable', 'disable', 'status'}:
                return HELP
            check_host(home)
            store = Store(home)
            try:
                info = execute(Service(store, host_guard=check_host), SimpleNamespace(monitor_action=action))
            finally:
                store.close()
            from hermes_cli.profiles import get_active_profile_name
            result = render(info, get_active_profile_name())
            if action == 'enable':
                result += '\n会話ごとに追加のAI抽出を行います。'
            return result
        except KiokukoError as error:
            if error.code in {'CURATION_CLI_REQUIRED', 'LOCAL_CLI_REQUIRED'}:
                return 'この操作はHermesの対話CLIで実行してください。'
            return f'監視を操作できませんでした ({error.code})。'
        except (OSError, ValueError, AttributeError, ImportError, RuntimeError):
            return '監視を操作できませんでした (MONITOR_UNAVAILABLE)。'
