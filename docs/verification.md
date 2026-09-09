# 検証記録

2026-09-09時点。Orca監視・経験記憶とCodex Responses対応を含むコードを固定Hermesで検証済みです。release用の全環境matrixは未完了です。

## 実行環境と結果

| 環境 | 対象 | 結果 |
|---|---|---|
| macOS arm64 / Python 3.12.13 | unit・SQLite integration・固定Hermes integration（2026-09-09） | 200 passed |
| macOS arm64 / Python 3.11.15 | unit・SQLite integration | 110 passed |
| macOS arm64 / Python 3.13.14 | unit・SQLite integration | 110 passed |
| distribution | sdistからwheel作成、SQL・plugin metadata・二つのentry point・Orca bundleとreader | 検証済み |

Hermesは`NousResearch/hermes-agent@13e72fb205b735df679e0fd5f5996a34ac4accc6`（0.21.0）に固定しています。[pin.json](../tests/hermes_e2e/pin.json)にarchiveと契約対象ソースのSHA-256を記録し、host fixtureはimport先の対象ファイルを照合します。別の0.21.xを同じ結果と見なしません。

## 実際に通した経路

- **実AIAgentの会話ループ**：実plugin discovery、MemoryProvider loader、tool middleware、SessionDB、completed同期。通常toolとTool Search bridgeの両方で、原文保存、候補作成、訂正、agent再作成後のresumeを検証。
- **host hook実行**：別スレッド、ContextVarの片方向伝達、timeout・skip・遅延完了、`api_content`へのmarker保存、prepared/observedの区別。
- **identity**：実Gateway ContextVar APIへ隔離した本人情報をbind。A/B・DM/groupの分離、process環境変数へfallbackしないことを検証。
- **Gateway再利用時のID欠落**：未改変の実`_set_session_env()`で受信ごとにIDが空へ戻る経路を使い、Kiokuko側だけで連続3ターンの候補保存に成功。初回だけIDを公開する場合・初回から空の場合の両方を検証。別ID・別profile・別送信者・別会話・別thread・未設定ID・session key欠落の拒否、同時に動く別DMへのアクセス拒否、モデル引数と環境変数をIDに使わないことを検証。Hermes本体とそのContextVarは修正しません。
- **CLI**：実parserで原文保存とretry、claim・scope・根拠・revisionの表示、承認取消・承認、purge、workspace対応づけを検証。
- **SQLite**：承認前のactive化拒否、revision競合、rewind、遅延session処理、履歴のscope、FTS/n-gram fallback、圧縮相当のmarker欠落、訂正通知、同時初期化、purgeと遅延job、backup/restore、異常DBの非破壊拒否を検証。
- **検証付きcompaction**：実Hermesの`_pre_compress_memory_context`→MemoryManager→providerとsession endを通過。抽出モデル応答は固定し、実ファイルを照合。誤値・型違い・未認証履歴・未完了turn・rewind・古いrootへのCWD混線・symlink・secretの拒否、要約の誤記、根拠変更後の検索除外と無効化、purge後の再作成拒否を検証。
- **curation**：インストールした`kioku-curation`を子processから起動し、実際の標準入力で選択・無効番号・確認から戻る・採用を実行。取消・EOF・Ctrl-C、共有範囲、batch競合時の全件rollback、候補更新後の有効な選択維持も試験。画面readerを用いた実機評価は未実施。
- **slash curation（v0.1.1）**：固定Hermesのplugin discoveryと`HermesCLI.process_command()`から一覧・番号選択・確認コード・採用を実行。重複確定、選択変更、期限切れ、session・workspace・profile・principal変更、rewind、根拠変更・訂正・purge時の全件中止、DB busy時の選択保持を試験。実Gatewayのplugin dispatcherによる拒否と、bindされたDM/groupの拒否を確認。対話CLI参照・稼働状態・非同期呼出しの境界を検証。端末画面を操作した実機評価やGatewayの実ネットワーク配送は未実施。
- **slash update（v0.1.1）**：固定Hermesへの登録、現在profileとPythonの固定、重複開始・venv lockによる競合拒否、失敗後のretry、受信イベントなしの呼出し拒否を試験。一時venvの模擬pipを実subprocessで起動し、引数・`HERMES_HOME`・pip設定の隔離・version確認を検証。異常終了・timeout・起動失敗を成功扱いせずlockを解放することを確認。PyPIからの実インストールや既存Hermes環境の更新はこの検証では実行していません。
- **slash monitor**：実CLI dispatcherで状態表示・有効化・停止を検証。現在profileへの限定、Node不在・不正引数時の非変更、受信イベントなしのGateway呼出し・委譲・実行中agentの拒否、稼働recorderの停止を含む10試験が成功。既存slash管理・監視integrationと合わせて45試験が成功。
- **Gateway管理コマンド**：固定Hermesの受信認証→command権限・hook→plugin dispatcherで、監視の状態確認・有効化・停止、更新の開始・statusを検証。DM/groupの権限、拒否hook、内部イベント、別タスク・引数・profileへの流用、コンテキストの再使用、同時受信を試験。認証の許可/拒否とpipは模擬結果を使い、Discordの実ネットワーク配送・リモートへのインストールは含みません。
- **追加migration**：v1の記憶とkeyを保持してv2へ移行し、v1 checksum不一致では移行しないことを検証。

- **Orca監視**：CLIとGatewayの実AIAgent→実OpenAI SDK→localhost HTTP→実Node writer→同梱Python readerを通過。通常応答・SSEからの集約応答・実tool loopを記録。固定抽出結果を保存し、次ターンの実HTTP要求へ未検証ラベル付きで注入。A/B・groupの同時middleware、429/500/timeout/キャンセル、監視ON/OFFでの要求・応答・実行回数の維持、補助モデルの実HTTP呼び出しとfallback禁止を検証。
- **Codex Responses監視（2026-09-09修正）**：未改変Gatewayの`_set_session_env()`をターンごとに通したPhotonの実AIAgentで、Responsesのtool loop→Orca記録→HermesのCodex抽出clientによるlocalhost要求→経験保存→次ターン注入を検証。`model.default`と`-900k`変換、終了イベントのoutputがnullの場合、失敗・未完了・完了イベントなしの拒否、SDK再試行なし、未対応方式の原因永続化も試験。実Codex認証・リモートHermes 0.21.1・Photonの配送はこの試験に含みません。
- **経験・障害境界**：v1/v2→v3、引用不一致、成功宣言だけの結果未確認化、secret・恒久指示・assistantだけの根拠の拒否、scope、独立観測と再検索の区別、期限・訂正・purge・rewind後の既存経験と遅延jobの失効を検証。Node起動失敗、queue/容量超過、未完了run・jobの復旧、抽出timeout後のlock保持、取得ゼロ、完了と最終応答の競合、events/blob破損・未知イベント・symlink・保持期限・元記録欠落も試験。
- **Orca配布物**：`40ed17c8b865952e6f0b31bf1df9876a9645814e`へ固定。bundle再生成後のdigest、上流ソース・同梱Python readerのchecksum一致を確認。実行時npmは不要。

上記の自動テストではLLMは決定的な応答です。監視試験ではlocalhostのHTTPサーバー、既存の会話試験ではHTTP transportの置換を使います。実モデルの抽出精度・網羅性・訂正への追従は測定していません。Gatewayの実ネットワーク接続、Telegram等の配送、Linux、Python 3.11/3.13上の全Hermes依存環境、performance目標はこの自動テストに含みません。テストは一時profileを使い、既存ユーザーprofileを変更しません。

`codex_app_server`・MoAは自動注入の対象外です。background review/delegation captureとsemantic retrieval/embedding consumerはoptional phaseであり、未提供です。vector試験はconsumerの完成を示すものではなく、purge後の遅延commitを拒否する境界の試験です。supersedeはDDLの削除規則を試験していますが、merge/supersede用CLIは公開していません。

## リモート実機での確認（2026-09-09・利用者提供ログ）

Ubuntu / Python 3.11 / Hermes 0.21.1、`main` profile、設定されたproviderは`openai-codex`、モデルは`gpt-5.6-luna-900k`。利用者がGatewayのsession IDパッチと修正版0.1.3 wheelを適用し、サービスを再起動しました。こちらからサーバーへ接続した検証ではありません。

- Gatewayパッチ適用後のPhoton会話で、`kiokuko_propose`が`ok: true / pending`を返し、候補数が7から8へ増加。
- wheel更新後の連続2ターンは両方`complete`、`dropped: 0`、`error_code: null`。API記録数は0から2、抽出jobは`done: 2`、最終抽出成功は`2026-09-09T08:18:02.983652+00:00`。
- 過去の`incomplete: 7`と既存エラーの件数・更新日時は、この2ターンでは増加なし。

この実機ログは候補保存と監視・抽出処理の復旧を示します。`jobs.done`は経験の採用件数が0でも成立するため、実モデルが有用な経験を保存し、次ターンへ注入したことまではこの短文テストで確認していません。ファイル検証付きcompactionの実機成功も未確認です。

この実機確認はHermes側パッチを使った時点の結果です。その後のKiokuko側だけでID欠落へ対応する変更は、上記の未改変Hermesによる自動テストで検証しており、リモートへの反映は未確認です。

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

wheelにはruntime package、SQL、plugin manifest、entry point、ビルド済みOrca bundle、固定Python reader、第三者licenseを含めます。Hermes本体、Node実行環境、DB、identity key、native memory、開発用cacheは含めません。package自体にHermesの重複依存を宣言せず、hostの既存環境と起動時の互換性検査を使います。
