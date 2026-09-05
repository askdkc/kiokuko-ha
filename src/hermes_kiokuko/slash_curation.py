"""Human-only, non-stdin curation in the audited interactive CLI dispatcher."""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import secrets
import threading
import time

from .compatibility import active_home, check_host
from .curation import adopt, render, review_all
from .errors import KiokukoError
from .identity import bound_values, resolve_identity
from .models import new_id
from .security import host_scan
from .service import Service
from .store import Store

HELP = """/kioku-curation — 検証済みのプロジェクト記憶をGlobalへ共有
/kioku-curation                 候補を再確認して表示
/kioku-curation select 1 3      番号のチェックを切替
/kioku-curation all             全選択
/kioku-curation none            選択解除
/kioku-curation show            現在の選択を表示
/kioku-curation share           選択した本文と共有先を確認
/kioku-curation cancel          共有せず終了
Globalは、このprofileの全利用者・全プロジェクトへの共有です。
チェックは文字表示です。各操作をslash commandとして送信してください。"""
UNSUPPORTED = ("この操作はHermesの対話CLIで実行してください。"
               "この呼出経路では管理者と現在のsessionを確認できないため、候補を表示・共有しません。"
               "端末からは、対象profileとプロジェクトで kioku-curation を実行できます。")
TTL = 15 * 60


def cli_binding(ctx):
    """No sender/environment fallback: require the host's attached local CLI.

    The audited gateway invokes handlers on an event loop before binding sender
    ContextVars. Deny that path even if a CLI is attached to the same manager.
    _cli_ref is an internal host contract, covered by the pinned dispatcher tests.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise KiokukoError("CURATION_CLI_REQUIRED")
    cli = getattr(ctx._manager, "_cli_ref", None)
    session = getattr(cli, "session_id", None)
    if not isinstance(session, str) or not session or getattr(cli, "_agent_running", False):
        raise KiokukoError("CURATION_CLI_REQUIRED")
    home = active_home()
    if Path(ctx._manager.home_path).resolve() != home:
        raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
    bound = bound_values()
    if any(bound.get(key) for key in ("USER_ID", "USER_ID_ALT", "CHAT_ID", "CRON")) or \
            bound.get("PLATFORM", "cli") != "cli":
        raise KiokukoError("CURATION_CLI_REQUIRED")
    return home, session


@dataclass
class Review:
    key: tuple
    snapshot: object
    items: list
    expires: float
    selected: set = field(default_factory=set)
    confirmation: str | None = None


class SlashCuration:
    def __init__(self, ctx):
        self.ctx = ctx
        self.review = None
        self.lock = threading.Lock()

    def __call__(self, raw_args):
        if not self.lock.acquire(blocking=False):
            return "再確認・共有の処理中です。完了後にもう一度操作してください。"
        try:
            home, session = cli_binding(self.ctx)
            check_host(home)
            with_store = Store(home)
            try:
                service = Service(with_store, host_guard=check_host, content_guard=host_scan)
                identity = resolve_identity(with_store, session, "cli")
                if identity.origin != "cli" or identity.principal_id != "profile-owner":
                    raise KiokukoError("CURATION_CLI_REQUIRED")
                snapshot = service.snapshot(session, new_id("curation"), raw_args, identity)
                return self.execute(service, snapshot, raw_args)
            finally:
                with_store.close()
        except KiokukoError as error:
            if error.code == "CURATION_CLI_REQUIRED":
                return UNSUPPORTED
            return f"操作できませんでした ({error.code})。設定とsessionを確認し、/kioku-curation でやり直してください。"
        except (OSError, ValueError, AttributeError, ImportError):
            return "操作できませんでした (CURATION_UNAVAILABLE)。kiokuko doctorで状態を確認してください。"
        finally:
            self.lock.release()

    def execute(self, service, snapshot, raw_args):
        key = (snapshot.profile_key, snapshot.session_id, snapshot.session_generation,
               snapshot.principal_id, snapshot.conversation_id, snapshot.workspace_id)
        review = self.review
        if review and (review.key != key or time.monotonic() >= review.expires):
            self.review = review = None
        args = raw_args.split()
        action = args[0] if args else "refresh"
        if action == "help" and len(args) == 1:
            return HELP
        if action == "cancel" and len(args) == 1:
            self.review = None
            return "共有せず終了しました。プロジェクトの記憶は保持しています。"
        if snapshot.workspace_id is None:
            return "プロジェクトを確認できません。対象ディレクトリでHermesを起動し直してください。"
        if action == "refresh" and len(args) <= 1:
            outputs = []
            # A failed refresh must not leave a previous approval armed.
            self.review = None
            items, _ = review_all(service, snapshot, output=outputs.append)
            if not items:
                return "\n".join(outputs + ["採用可能な候補はありません。", HELP])
            review = Review(key, snapshot, items, time.monotonic() + TTL)
            self.review = review
            return "\n".join(outputs + [self.display(review), HELP])
        if review is None:
            return "候補が未表示、期限切れ、またはsession・プロジェクトが変わりました。/kioku-curation で再確認してください。"
        if action == "select" and len(args) > 1:
            try:
                numbers = {int(n) for n in args[1:]}
                if any(n < 1 or n > len(review.items) for n in numbers):
                    raise ValueError
            except ValueError:
                return "表示されている番号を指定してください。選択は維持しています。\n" + self.display(review)
            review.selected.symmetric_difference_update(numbers)
            review.confirmation = None
        elif action in {"all", "none"} and len(args) == 1:
            review.selected = set(range(1, len(review.items) + 1)) if action == "all" else set()
            review.confirmation = None
        elif action == "show" and len(args) == 1:
            pass
        elif action == "share" and len(args) == 1:
            if not review.selected:
                return "共有する番号を /kioku-curation select 1 の形式で選択してください。"
            review.confirmation = secrets.token_hex(8)
            return (self.display(review, chosen_only=True) +
                    "\n選択した本文をこのprofileの全利用者・全プロジェクトへ共有します。\n"
                    f"確定: /kioku-curation confirm {review.confirmation}\n"
                    "変更: /kioku-curation select 番号 / 取消: /kioku-curation cancel")
        elif action == "confirm" and len(args) == 2:
            if not review.confirmation or not secrets.compare_digest(args[1], review.confirmation):
                return "確認コードが無効です。/kioku-curation share で現在の共有内容を確認してください。"
            try:
                ids = adopt(service, review.snapshot, [review.items[n - 1] for n in sorted(review.selected)])
            except KiokukoError as error:
                review.confirmation = None
                if error.code == "STORE_BUSY":
                    return "DBが使用中です。選択は維持しています。/kioku-curation share から再試行してください。"
                self.review = None
                return f"共有は0件です ({error.code})。根拠または記憶が変わりました。/kioku-curation で候補を再確認してください。"
            self.review = None
            return f"{len(ids)} 件をGlobal記憶へ採用しました。\n" + "\n".join(ids)
        else:
            return "操作を認識できません。選択は維持しています。\n" + HELP
        return self.display(review) + "\n共有内容を確認: /kioku-curation share / 操作一覧: /kioku-curation help"

    @staticmethod
    def display(review, *, chosen_only=False):
        outputs = []
        if chosen_only:
            items = [review.items[n - 1] for n in sorted(review.selected)]
            render(items, set(range(1, len(items) + 1)), outputs.append)
        else:
            render(review.items, review.selected, outputs.append)
        return "\n".join(outputs)
