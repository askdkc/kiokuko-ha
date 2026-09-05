# 検証記録

2026-09-05時点。MVPのコードと固定Hermesによる統合試験を実装済みです。release用の全環境matrixは未完了です。

## 実行環境と結果

| 環境 | 対象 | 結果 |
|---|---|---|
| macOS arm64 / Python 3.12.13 | unit・SQLite integration・Hermes integration（v0.1.1） | 107 passed |
| macOS arm64 / Python 3.11.15 | unit・SQLite integration | 69 passed |
| macOS arm64 / Python 3.13.14 | unit・SQLite integration | 69 passed |
| distribution | sdistからwheel作成、SQL・plugin metadata・二つのentry point | 検証済み |

Hermesは`NousResearch/hermes-agent@13e72fb205b735df679e0fd5f5996a34ac4accc6`（0.21.0）に固定しています。[pin.json](../tests/hermes_e2e/pin.json)にarchiveと契約対象ソースのSHA-256を記録し、host fixtureはimport先の対象ファイルを照合します。別の0.21.xを同じ結果と見なしません。

## 実際に通した経路

- **実AIAgentの会話ループ**：実plugin discovery、MemoryProvider loader、tool middleware、SessionDB、completed同期。通常toolとTool Search bridgeの両方で、原文保存、候補作成、訂正、agent再作成後のresumeを検証。
- **host hook実行**：別スレッド、ContextVarの片方向伝達、timeout・skip・遅延完了、`api_content`へのmarker保存、prepared/observedの区別。
- **identity**：実Gateway ContextVar APIへ隔離した本人情報をbind。A/B・DM/groupの分離、process環境変数へfallbackしないことを検証。
- **CLI**：実parserで原文保存とretry、claim・scope・根拠・revisionの表示、承認取消・承認、purge、workspace対応づけを検証。
- **SQLite**：承認前のactive化拒否、revision競合、rewind、遅延session処理、履歴のscope、FTS/n-gram fallback、圧縮相当のmarker欠落、訂正通知、同時初期化、purgeと遅延job、backup/restore、異常DBの非破壊拒否を検証。
- **検証付きcompaction**：実Hermesの`_pre_compress_memory_context`→MemoryManager→providerとsession endを通過。抽出モデル応答は固定し、実ファイルを照合。誤値・型違い・未認証履歴・未完了turn・rewind・古いrootへのCWD混線・symlink・secretの拒否、要約の誤記、根拠変更後の検索除外と無効化、purge後の再作成拒否を検証。
- **curation**：インストールした`kioku-curation`を子processから起動し、実際の標準入力で選択・無効番号・確認から戻る・採用を実行。取消・EOF・Ctrl-C、共有範囲、batch競合時の全件rollback、候補更新後の有効な選択維持も試験。画面readerを用いた実機評価は未実施。
- **slash curation（v0.1.1）**：固定Hermesのplugin discoveryと`HermesCLI.process_command()`から一覧・番号選択・確認コード・採用を実行。重複確定、選択変更、期限切れ、session・workspace・profile・principal変更、rewind、根拠変更・訂正・purge時の全件中止、DB busy時の選択保持を試験。実Gatewayのplugin dispatcherによる拒否と、bindされたDM/groupの拒否を確認。対話CLI参照・稼働状態・非同期呼出しの境界を検証。端末画面を操作した実機評価やGatewayの実ネットワーク配送は未実施。
- **slash update（v0.1.1）**：固定Hermesへの登録、現在profileとPythonの固定、重複開始・venv lockによる競合拒否、失敗後のretry、Gateway拒否を試験。一時venvの模擬pipを実subprocessで起動し、引数・`HERMES_HOME`・pip設定の隔離・version確認を検証。異常終了・timeout・起動失敗を成功扱いせずlockを解放することを確認。PyPIからの実インストールや既存Hermes環境の更新はこの検証では実行していません。
- **追加migration**：v1の記憶とkeyを保持してv2へ移行し、v1 checksum不一致では移行しないことを検証。

会話ループのLLM HTTP通信と、compaction用の追加モデル呼び出しを決定的な応答へ置換しています。実モデルの抽出精度・網羅性・訂正への追従は測定していません。Gatewayの実ネットワーク接続、Telegram等の配送、Linux、Python 3.11/3.13上の全Hermes依存環境、performance目標は未検証です。テストは一時profileを使い、既存ユーザーprofileを変更しません。

`codex_app_server`・MoAは自動注入の対象外です。background review/delegation captureとsemantic retrieval/embedding consumerはoptional phaseであり、未提供です。vector試験はconsumerの完成を示すものではなく、purge後の遅延commitを拒否する境界の試験です。supersedeはDDLの削除規則を試験していますが、merge/supersede用CLIは公開していません。

## 再実行

Python 3.12を推奨します。開発用環境はcheckout内へ作成します。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest tests/unit tests/integration -q
```

Hermes統合試験は固定hostを別途installします。取得scriptは既存の`.cache/hermes`を上書きしません。archive checksumが変わった場合も停止します。

```sh
.venv/bin/python scripts/fetch_hermes_fixture.py
.venv/bin/python -m pip install -e .cache/hermes
.venv/bin/python -m pytest -q
```

host未install時はHermes suiteがskipされます。**coreのみの合格やskipをhost E2E合格として扱わないでください。** hostが存在してpinが不一致ならskipせず失敗します。

配布物の作成：

```sh
.venv/bin/python -m build
```

wheelにはruntime package、SQL、plugin manifest、entry pointだけを含めます。Hermes本体、DB、identity key、native memory、開発用cacheは含めません。package自体にHermesの重複依存を宣言せず、hostの既存環境と起動時の互換性検査を使います。
