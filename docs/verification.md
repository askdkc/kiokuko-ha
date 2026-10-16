# 検証記録

各結果は記載日時・対象環境の記録です。現在の再実行手順は以下を使用します。release用の全環境matrixは未完了です。

## 現在の検証手順：能力で判定し、その回のビルドだけを使う

Pythonは標準の[`tomllib`](https://docs.python.org/3/library/tomllib.html)を直接使うため
`>=3.11`が必要です。未検証という理由だけで置いていたPythonとPyYAMLの上限を削除しました。
TOMLの解析、PyYAMLの安全なroundtrip・型保持・Python object tagの拒否、Hermesの
必須API・keyword・実同期動作を検査します。版数を書き換えた試験は能力判定の回帰試験であり、
未実行のPython/PyYAML新版全体が動く証明ではありません。

`--host`には検証したい実際のHermes checkoutを指定します。現在版の検証では
`KIOKUKO_HERMES_FIXTURE`を設定しません。PM検証scriptの`--fixture`は、明示した旧版の
checksumを再現するときだけ追加します。fixtureカタログは本番・現在版の許可リストではありません。

以下は依存関係を用意した検証用venvで実行します。venv名はこのMacでの例です。
`verification_support.py build`は毎回新しい出力先へsdist→wheelを作り、両方のmetadataが
現在の`pyproject.toml`と一致することを確認してmanifestを返します。既存の`dist/`から
古い配布物を選びません。manifestから取得するときも場所とSHA-256を確認します。

```sh
hermes_host=/absolute/path/to/hermes-agent
unset KIOKUKO_HERMES_FIXTURE
PYTHONPATH="$PWD/src:$hermes_host" .venv-py314/bin/python -m pytest -q
manifest=$(.venv/bin/python scripts/verification_support.py build)
wheel=$(.venv/bin/python scripts/verification_support.py artifact "$manifest" wheel)
archive=$(.venv/bin/python scripts/verification_support.py artifact "$manifest" sdist)
PYTHONPATH="$hermes_host" .venv-py314/bin/python scripts/verify_research_package.py "$wheel"
.venv-py314/bin/python scripts/verify_native_plugin.py "$archive" --host "$hermes_host"
.venv-py314/bin/python scripts/verify_pm_lifecycle.py "$archive" --host "$hermes_host"
.venv-py314/bin/python scripts/verify_update_package.py "$wheel" --host "$hermes_host"
```

PMのAPIだけを先に調べる場合は、配布物を渡さず
`verify_pm_lifecycle.py --host "$hermes_host" --check-host-only`を使えます。
実導入の検証をこの事前検査だけで成功扱いにはしません。

残す固定値は、自パッケージ版、標準APIに対応するPython下限と従来の依存下限、明示的な旧版回帰fixture、
同梱Orcaコードのcommit・bundle hash・元lock解決値、DB/trace/wireの形式番号です。
Nodeも版数比較を撤去し、同梱writerの実起動・protocol handshakeで判定します。traceや設定は作成せず、結果は実行ファイルとbundleの識別に対してcacheします。bundleのbuild targetはnode22のままです。
読込済みコードとディスク上の版・fingerprintの照合は更新後の再起動診断です。

この変更の限定検証では、Python/PyYAMLの旧制限で9件失敗し、修正後の現在host・原文探索・
署名継続を含む67件が成功しました。一時コピーへ制限を戻すと同じ9件が失敗します。
Nodeの版数だけ不明な正常runtimeは旧判定で失敗し、実writer検査とYAMLの型保持検査への修正後は関連80件が成功、
旧判定を戻すと再び失敗します。API/keyword欠落、unsafe YAML、不正writer protocolも拒否します。
現在hostでの実PM導入・更新・cold boot・失敗時の状態保持も成功しています。
以下の685件はこの上限撤去とNode判定変更より前の結果です。現変更の全体試験は未実施です。

## 2026-10-16：0.1.20のcontext失敗処理と署名のredaction衝突

context準備が`STORE_BUSY` / `DEADLINE_EXCEEDED`で失敗した場合、通常のmodel・tool処理を
続けながら、そのhookが新規作成したcaptureだけを`incomplete`として閉じます。
同じturnへの重複hookや古いcaptureから別のcaptureを閉じないことも検証しました。
署名なしのエラーcontextを配信成功にはせず、候補・経験・承認済み記憶を作成しません。
doctorの`ok`は準備状態だけを示し、`answer_application_verified`は未確認の`null`です。

最終試験で一度残ったstreaming captureは、限定28ケースでも再現しました。
enqueue時とprovider受信時の記録から、delivery ID末尾の数字10桁と直後のHMACが
HermesのTelegram token除去regexに一致し、署名が`***`になることを確認しました。
署名前のASCII空白でこの衝突を避け、従来の空白なしmarkerも読み取ります。
HMAC・本文digest・元user rowの検証条件は維持しています。

- 数字8桁で終わるIDを固定した実AIAgent・HTTP streaming試験：修正前は1件失敗。
- 修正後のCLI/Telegram・streaming/非streaming・2ターンの経験取得、context失敗、
  monitor、旧新markerと原文・本文・署名改ざんの拒否：関連56件成功。
- 公式Hermes 0.22.1 / Python 3.14.7の標準`pytest -q`：**685 passed**、
  upstream非推奨API警告92件、141.89秒。skip・除外なし。試験中のソース変更なし。
  Git外の専用一時ディレクトリと隔離profileを用い、終了後に専用一時物を削除。
- timeout・待機時間・成功条件・依存上限・実行時の版数許可リストは追加・緩和していません。

これは実HTTP transportをローカルの模擬modelで通した検証です。実モデルの回答への
意味的な適用、実Gateway配送、remote CI、全Python/OS/Hermesの組合せは未確認です。
以前の負荷試験で観測した0.5秒のcontext期限という制約も残ります。

## 2026-10-16：Hermesの版数制限をAPI・同期動作検査に置換

`0.21.x`正規表現と、版数不明時のfingerprint一致による起動制限を削除しました。
release identityは表示にだけ使います。必要なmemory/plugin API、実際に渡すkeyword、
Gatewayのsession ContextVarを検査し、`MemoryManager`の公開APIから静的な試験文字列を
メモリ内の仮providerへ同期します。callback・messages・session ID・ContextVarの伝達を
確認し、providerとexecutorを終了します。実装単位で結果をcacheし、通常のtool呼出しで
毎回threadを生成しません。実メモリ、実ユーザーの文面、LLM、web検索、認証は使いません。

- 公式[v0.22.1](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.1)のcommitは
  `297552a9d646119871391d202509822cd86330df`。archiveと必要ソースのchecksumは
  [pin.json](../tests/hermes_e2e/pin.json)の試験fixtureに記録しています。
  これは実行時の許可リストではありません。stampのないarchiveも同じAPI検査を通します。
- 公式main `6206f89320f3506c901948be0f487eae7f2b0d71`の必要APIは一次ソースで照合しました。
  Skill登録は`hermes_cli.plugins_content`から`PluginContext`へ公開されています。
  main全体の起動・依存解決をこの照合だけで確認済みとは扱いません。
- 変更前はAPIが同じ0.22.1・将来minor/patch・major・版数不明の7ケースすべてが失敗。
  修正後に旧`0.21.x`条件だけを一時コピーへ戻すmutationでも同じ7件が失敗しました。
  この回帰試験は通常の`python -m pytest -q`で収集されます。
- 必須APIの欠落、keyword非互換、同期callback欠落、payload/context伝達の不一致は拒否。
  失敗箇所は`failed_checks`に示し、例外の自由文は出力しません。
  実0.21.0/Python 3.14の壊れたexecutorは
  `HOST_CONTRACT_MISMATCH / agent.memory_manager.sync_roundtrip / AttributeError`で拒否しました。
  0.21.0/Python 3.12の検索・保存承認経路は成功しています。
- 0.22.1で修正されたGatewayのID設定を受理しつつ、旧ID欠落を明示的に作る試験も維持。
  sender/profile/sessionの拒否、native memoryの選択制約は緩めていません。

全体監査では、Hermes releaseを制限する他の実行時条件はありませんでした。
旧版に言及するidentity/SDK adapterのコメントは実際の互換処理の説明です。
この時点で残っていたPython上限とPyYAML上限は、冒頭の現在の能力検査で撤去しました。
Node監視bundleのbuild target、DB migration/checksum・Orca wire/traceの形式番号、
loaded/disk版数の再起動診断は別の契約です。
source fingerprintは引き続き検証履歴の表示だけです。

実行結果：

- Python 3.14.7 + 0.22.1、標準`pytest -q`：**681 passed**、hostの非推奨API警告92件。
- Python 3.12.13 + 0.21.0：実AIAgent・検索・Gateway binding・診断の92件成功。
- Python 3.14.7 + 旧現行`88c6085`：同じ92件成功。
- wheelを一時targetへ実導入し、srcを検索pathから外した0.22.1試験：61件成功。
  provider directory・entry point・Skill/ライセンス・診断も成功。
- 0.22.1の実PMによるsdist依存解決・導入、候補→人の承認→永続化後の取得が成功。
  wheelのoffline実再導入と0.21.4/0.22.1の実CLI/Gateway更新dispatchも成功。
  更新job自体は模擬で、本番の更新は実行していません。
- sdist→wheel作成、twine検査、compileall、`git diff --check`が成功。

並行して複数の重い検証を動かした全体試験では、既存の0.5秒期限内に処理できず
`STORE_BUSY`/`DEADLINE_EXCEEDED`に関連する3件が失敗しました。監視の単独13件と、
並行負荷を外した上記681件では成功。期限・DB設定・成功条件を変更せず再確認しています。
負荷時に記憶contextを提供できない場合があるという既存の制約は残ります。

再実行には冒頭の現在の検証手順を使用します。以下の結果は記載したfixtureに対する過去の実行記録です。

一時profileの実Hermes dispatcher・AIAgent・middleware・SessionDB・PMを使用します。
外部LLM/web配送は模擬で、実モデルの品質、最新mainの全体実行、実Gatewayの配送は未確認です。
本番設定・auth・メモリDB・サービス、他repository、公開版数は変更していません。
commit/tag/push/公開は行っていません。静的型検査の設定はこのrepositoryにはありません。

## 2026-10-06：Python 3.14と現行PMの検証

- Python 3.14.7 + 現行Hermes `88c60858468d7adee27a752242c7c507fa4129d0`：unit/SQLite integration 357件、Hermes integration 224件、計581件成功。Gateway側の非推奨API警告80件あり。
- Python 3.12.13 + Hermes 0.21.0：同じ581件成功。
- 0.1.14のnativeソース配布物を、現行Hermes PMの実`lock_and_sync`と`PythonEnvironment`で新しいworkspaceへビルド・導入。依存関係を実際に解決し、`pip check`相当の整合性検査、user directory discovery、3 tools、managed更新案内、MemoryProviderのinitialize/shutdown、Hermes `doctor_plugin`と`validate_plugin_dir`を通過。uvエンジンには明示したローカル実行ファイルを使い、Hermes本体のtoolchain取得・本番selectionの切替は試験していません。
- 配布元と同じ追跡対象ファイル＋追加ファイルのsnapshotは、現行Hermes Plugin Guardで`safe`。防御用正規表現の拒否動作も保持。
- Python 3.14のwheel実導入・再導入：0.21.0を起動時に拒否し、0.21.4のregistryとGateway/CLI更新経路は成功。更新jobは模擬で、wheel導入自体は実pipのoffline導入。

この時点では`__version__`が0.0.0の場合にcanonicalな`get_version_info()`を確認し、identityもない場合は固定ソースのfingerprintとPython 3.14の一致を要求していました。この版数・fingerprintによる起動制限は、2026-10-16のAPI・同期動作検査で置き換えています。

native版はPMへ更新を委ねます。旧pip用の自己更新は保持しているため、導入検査の合格を公式カタログ規約の承認とは扱いません。GitHubへのpush、PyPI公開、公式カタログ掲載、利用者Gatewayへの導入・再起動・実ネットワーク配送は未実施です。このリポジトリにGitHub Actionsの検証workflowはなく、リモートCI成功は主張しません。

この日時の旧hostを明示して再現する場合（配布物`$archive`は冒頭の現ビルド手順から取得）：

```sh
.venv-py314/bin/python scripts/fetch_hermes_fixture.py --fixture current-88c6085
KIOKUKO_HERMES_FIXTURE=current-88c6085 \
  PYTHONPATH="$PWD/src:$PWD/.cache/hermes-current-88c6085" \
  .venv-py314/bin/python -m pytest tests/unit tests/integration tests/hermes_e2e -q
.venv-py314/bin/python scripts/verify_native_plugin.py "$archive" \
  --host .cache/hermes-current-88c6085 --fixture current-88c6085
```

native検証scriptは`.cache`内の使い捨てprofileとworkspaceだけを使います。PM fixtureの呼出元には`tomli-w`等の現行PM依存関係とPython 3.14、実uvが必要です。ネットワークが必要なのは依存関係の解決・取得で、実LLMやGatewayの外部送信は使いません。

## 過去の実行環境と結果

| 環境 | 対象 | 結果 |
|---|---|---|
| macOS arm64 / Python 3.12.13 | unit・SQLite integration・固定Hermes integration（2026-09-09） | 200 passed |
| macOS arm64 / Python 3.11.15 | unit・SQLite integration | 110 passed |
| macOS arm64 / Python 3.13.14 | unit・SQLite integration | 110 passed |
| distribution | sdistからwheel作成、SQL・plugin metadata・二つのentry point・Orca bundleとreader | 検証済み |

Hermes fixtureは0.21.0（`13e72fb205b735df679e0fd5f5996a34ac4accc6`）と0.21.4（`e794bb31cb75d2dc8937f928e736fb73155e674c`）を保持します。[pin.json](../tests/hermes_e2e/pin.json)にarchiveと契約対象ソースのSHA-256を記録し、host fixtureはimport先の対象ファイルを照合します。別の0.21.xを同じ結果と見なしません。

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
.venv/bin/python scripts/fetch_hermes_fixture.py --fixture 0.21.0
.venv/bin/python -m pip install -e .cache/hermes
.venv/bin/python -m pytest tests/hermes_e2e -q
.venv/bin/python scripts/fetch_hermes_fixture.py --fixture 0.21.4
KIOKUKO_HERMES_FIXTURE=0.21.4 PYTHONPATH=.cache/hermes-0.21.4:src \
  .venv/bin/python -m pytest tests/hermes_e2e -q
```

host未install時はHermes suiteがskipされます。**coreのみの合格やskipをhost E2E合格として扱わないでください。** hostが存在してpinが不一致ならskipせず失敗します。

配布物の作成：

```sh
.venv/bin/python -m build
```

wheelにはruntime package、SQL、plugin manifest、entry point、ビルド済みOrca bundle、固定Python reader、第三者licenseを含めます。Hermes本体、Node実行環境、DB、identity key、native memory、開発用cacheは含めません。package自体にHermesの重複依存を宣言せず、hostの既存環境と起動時の互換性検査を使います。


## Gateway更新コマンドの回帰確認

同期handlerはGateway envelopeを捕捉したcoroutineを返し、hostが元taskでawaitした
時点でctx・task・現在のenvelopeを照合して一度だけ消費します。executorでは消費しません。
contextのコピーも消費状態を共有します。照合に失敗したGateway呼出しはCLIへfallbackしません。
認証後のI/Oは従来のto_threadに残し、CLIのloop拒否、pipのpackage/index/venv制約を維持します。
Hermes 0.21.4で移動したSDK変換helperは新moduleから取得し、0.21.0の経路も維持します。

doctorのhost_contract.okはimport/APIとメモリ内同期試験の結果です。dispatch_contractは固定ソースの
fingerprint一致を別に示します。runtime_exercised=falseのため、doctorだけで実Gateway配送が
成功したとは判断できません。未検証sourceはunverifiedです。fingerprint不一致だけでは起動を拒否しません。
現在はPython 3.11以降で上限なしです。Hermesの版数は制限せず、必要なAPIと記憶同期の動作で判定します。旧0.21.0のDaemonThreadPoolExecutorはPython 3.14で削除された_initializer/_initargsを参照するため、版数条件ではなく実同期の失敗で拒否されます。

両hostを別processで実行します。archive/source checksum不一致は失敗とし、module cacheを
混在させません。localhost模擬HTTP serverを使うsuiteにはloopback listenerの許可が必要です。
GatewayRunnerとCLIは部分初期化し、認証結果・transport・pipは模擬です。
通常startup、実Telegram配送、PyPIでの公開後更新は別途確認が必要です。

wheel導入後の試験（`$wheel`と`$hermes_host`は冒頭の手順で準備）:

```sh
.venv/bin/python scripts/verify_update_package.py "$wheel" --host "$hermes_host"
```

このscriptは使い捨てvenvへローカルwheelを実pipで2回導入し、選択したhostのregistry・実Gateway
dispatch・CLI process_commandを検査します。Gatewayからのpip更新は模擬であり、実pip試験は
offlineのローカルwheel再導入です。本番PyPIや利用者のHermes環境にはアクセスしません。

2026-10-06の修正前再現では、0.21.4の実Gateway update/statusとexecutor→元taskの試験が
3 platformで失敗（6件）しました。初回修正後の件数はunit/integration 331件、
各Hermes suite 224件です。status/helpの非更新、認証否定、envelope盗用/replay、
cancel後のjob観測とlock解放、CLIの4操作、monitor/research/curationも含みます。
実PyPI更新、通常startup、実Telegram配送、公開・導入・再起動は未実施です。

## PM 導入と Gateway 読み込みの回帰試験

`runtime_provenance` は読み込み済みコードと現在の PM 選択を照合します。同じ Python・同じ版でも foreign source、旧 generation、同一版のコード変更を区別します。標準ライブラリだけの directory entry 診断は、package 欠落と内部依存欠落を分け、元の例外を再送出します。

```sh
.venv-py314/bin/python scripts/verify_native_plugin.py "$archive" --host "$hermes_host"
.venv-py314/bin/python scripts/verify_pm_lifecycle.py "$archive" --host "$hermes_host"
.venv-py314/bin/python scripts/verify_update_package.py "$wheel" --host "$hermes_host"
```

公開PM試験は使い捨てhomeへhostをコピーし、ローカルGit fixtureを公開install/updateコマンドで導入します。Hermesの通常ランチャー生成APIによる同一コマンドから起動し、bootstrapの環境選択で4 commands・3 tools・provider lifecycleを確認し、同一版更新前からのprocessと再起動後を照合します。無効なTOMLで更新を拒否した際、selection・config・DB・別profileが保持されることも検査します。Kiokukoのsrc/site-packagesを試験から注入しません。初期host依存には呼出元の検証用venvを使い、以降のgenerationはPMが作ります。管理toolchainと依存の取得にはネットワークが必要です。

Gatewayの認証・hook・dispatchは固定hostの別integration試験で検証します。cold boot試験は登録・provider・status handlerまでで、Discordとの接続や外部配送は行いません。実機復旧の証明には担当Gateway再起動後の/commandsと/kiokuko-update statusが別途必要です。リモートCI・公開・本番導入はこの試験に含みません。
