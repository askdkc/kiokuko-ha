"""Human review of freshly checked project facts; no model approval surface."""
import json

from .errors import KiokukoError
from .facts import attach_fact, verify_predicate
from .models import TurnSnapshot, canonical, digest
from .service import insert
from .workspace import resolve_workspace


def checked_item(service, db, entry_id):
    entry = service._entry(db, entry_id, admin=True)
    if entry["state"] != "active" or entry["scope_type"] not in {"principal_workspace", "conversation_workspace"}:
        raise KiokukoError("CURATION_SOURCE_UNAVAILABLE")
    fact = db.execute("""SELECT f.*,r.canonical_root,r.identity_hash FROM verified_facts f
        JOIN snapshot_roots r USING(profile_key,session_id,turn_id)
        WHERE f.entry_id=? AND f.entry_revision=?""", (entry_id, entry["current_revision"])).fetchone()
    if fact is None or resolve_workspace(fact["canonical_root"]) != fact["identity_hash"]:
        raise KiokukoError("FACT_WORKSPACE_UNAVAILABLE")
    checked = verify_predicate(fact["canonical_root"], json.loads(fact["predicate_json"]))
    # Preserve the project's applicability. A local fact does not establish a
    # universal rule merely because a person shares it across their profile.
    claim = f"プロジェクト {entry['workspace_id']} に関する観測: {checked['claim']}"
    service.validate_content(claim)
    key = digest(canonical(["curation", entry_id, entry["current_revision"], claim]))
    return {"entry": entry, "fact": dict(fact), "checked": checked,
            "global_claim": claim, "receipt_hash": key}


def review_all(service, snapshot, *, output=print):
    with service.transaction(snapshot) as db:
        ids = [r[0] for r in db.execute("""SELECT e.id FROM memory_entries e
            JOIN verified_facts f ON f.entry_id=e.id AND f.entry_revision=e.current_revision
            WHERE e.workspace_id=? AND e.state='active' AND e.scope_type IN ('principal_workspace','conversation_workspace')
            ORDER BY e.created_at,e.id""", (snapshot.workspace_id,))]
    output(f"現在のプロジェクトの検証済み記憶 {len(ids)} 件を再確認します。")
    items, unavailable = [], 0
    for number, entry_id in enumerate(ids, 1):
        try:
            with service.transaction(snapshot) as db:
                item = checked_item(service, db, entry_id)
                if db.execute("SELECT 1 FROM fact_receipts WHERE receipt_hash=?", (item["receipt_hash"],)).fetchone():
                    output(f"{number}/{len(ids)}: 共有操作済み（再採用しません）")
                    continue
            item["review_digest"] = digest(canonical(item))
            items.append(item)
            output(f"{number}/{len(ids)}: 根拠を確認しました。")
        except KiokukoError as error:
            unavailable += 1
            output(f"{number}/{len(ids)}: 採用対象外 ({error.code})。元ファイルや記憶の状態を確認してください。")
    return items, unavailable


def adopt(service, snapshot, items):
    with service.transaction(snapshot, write=True) as db:
        fresh = []
        for item in items:
            current = checked_item(service, db, item["entry"]["id"])
            if current["entry"]["workspace_id"] != snapshot.workspace_id or digest(canonical(current)) != item["review_digest"]:
                raise KiokukoError("APPROVAL_CHANGED")
            if db.execute("SELECT 1 FROM fact_receipts WHERE receipt_hash=?", (current["receipt_hash"],)).fetchone():
                raise KiokukoError("CURATION_ALREADY_APPLIED")
            fresh.append(current)
        results = []
        for item in fresh:
            entry = service._create(db, snapshot, item["global_claim"], "principal", approved=True, kind="project_fact")
            entry = service._change(db, entry, "share", scope="profile", approved=True)
            fact = item["fact"]
            row = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?",
                             (fact["profile_key"], fact["session_id"], fact["turn_id"])).fetchone()
            source = TurnSnapshot(**{k: row[k] for k in TurnSnapshot.__dataclass_fields__})
            attach_fact(db, entry, source, item["checked"])
            insert(db, "fact_receipts", {"receipt_hash": item["receipt_hash"], "entry_id": entry["id"]})
            results.append(entry["id"])
        for item, reviewed in zip(fresh, items):
            if digest(canonical(checked_item(service, db, item["entry"]["id"]))) != reviewed["review_digest"]:
                raise KiokukoError("FACT_SOURCE_CHANGED")
    return results


def render(items, selected, output):
    output("\nGlobal候補（このHermes profileの全利用者・全プロジェクトに共有）")
    output("プロジェクト内の元の記憶は保持します。未選択の項目は共有しません。")
    for index, item in enumerate(items, 1):
        entry, checked = item["entry"], item["checked"]
        output(f"[{'x' if index in selected else ' '}] {index}. {item['global_claim']}")
        output(f"    出典: {checked['predicate']['path']} / 元の範囲: {entry['scope_type']} / 所有者: {entry['principal_id'] or entry['conversation_id']}")
        output(f"    記憶: {entry['id']} revision {entry['current_revision']}")
    output(f"選択: {len(selected)} / {len(items)} 件")


def curate(service, snapshot, *, input_fn=input, output=print):
    if snapshot.workspace_id is None:
        raise KiokukoError("SCOPE_UNAVAILABLE")
    items, unavailable = review_all(service, snapshot, output=output)
    if not items:
        output("採用可能な候補はありません。compact時・会話終了時に現在のファイルで確認できた項目が対象です。")
        return {"adopted": [], "unavailable": unavailable}
    selected = set()
    while True:
        render(items, selected, output)
        try:
            answer = input_fn("番号でチェック切替（例: 1 3） / a: 全選択 / n: 選択解除 / s: 選択分を共有 / q: 終了 > ").strip()
            if answer == "q":
                output("共有せず終了しました。プロジェクトの記憶は保持しています。")
                return {"adopted": [], "cancelled": True}
            if answer == "a":
                selected = set(range(1, len(items) + 1))
            elif answer == "n":
                selected.clear()
            elif answer == "s":
                if not selected:
                    output("共有する項目を番号で選択してください。")
                    continue
                chosen = [items[i - 1] for i in sorted(selected)]
                output(f"選択した {len(chosen)} 件を、このprofileの全利用者・全プロジェクトへ共有します。")
                if input_fn("共有する: share / 戻る: Enter > ").strip() != "share":
                    continue
                output("根拠とrevisionを再確認して保存しています…")
                try:
                    ids = adopt(service, snapshot, chosen)
                except KiokukoError as error:
                    output(f"共有できませんでした ({error.code})。今回の共有は0件です。")
                    # A busy database can be retried without losing the selection;
                    # changed evidence requires a fresh displayed review.
                    if error.code == "STORE_BUSY":
                        continue
                    chosen_ids = {i["entry"]["id"] for i in chosen}
                    output("候補を更新します。引き続き有効な項目の選択は維持します。更新後の本文を確認してください。")
                    items, unavailable = review_all(service, snapshot, output=output)
                    selected = {i for i, item in enumerate(items, 1) if item["entry"]["id"] in chosen_ids}
                    if not items:
                        output("採用可能な候補がなくなったため終了します。")
                        return {"adopted": [], "error": error.code}
                    continue
                output(f"{len(ids)} 件をGlobal記憶へ採用しました。")
                return {"adopted": ids}
            else:
                try:
                    numbers = {int(n) for n in answer.split()}
                    if not numbers or any(n < 1 or n > len(items) for n in numbers):
                        raise ValueError
                    selected.symmetric_difference_update(numbers)
                except ValueError:
                    output("表示されている番号、a / n / s / q を入力してください。選択は維持しています。")
        except (EOFError, KeyboardInterrupt):
            output("\n共有せず終了しました。プロジェクトの記憶は保持しています。")
            return {"adopted": [], "cancelled": True}


def setup_parser(parser):
    parser.description = "プロジェクトの検証済み記憶を再確認し、選択した項目をGlobalへ共有します。"
    parser.set_defaults(kiokuko_action="curation")


def main(argv=None):
    import argparse
    from .cli import cli_handler
    parser = argparse.ArgumentParser(prog="kioku-curation")
    setup_parser(parser)
    return cli_handler(parser.parse_args(argv))
