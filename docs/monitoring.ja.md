# API監視と経験記憶

OrcaReplayの記録処理を使い、Hermesの**middleware境界の要求・応答**を保存します。OpenAI互換Chat Completionsと、`openai-codex`を含むHermesの`codex_responses`経路のCLI・Gatewayが対象です。HTTP傍受、SSEチャンクの生データ、SDK内部の再試行、完全再生は対象外です。Hermes本体やAPIの接続先は変更しません。

## 有効化

Node.js 22.12以降を別途用意し、対象profileのHermes対話CLIで実行します。

```text
/kiokuko-monitor enable
/kiokuko-monitor status
/kiokuko-monitor disable
```

引数なしは状態表示です。端末コマンドも利用できます。

```sh
hermes kiokuko monitor enable
hermes kiokuko monitor status
hermes kiokuko doctor
```

Node用bundleと、同じ上流commitのPython readerは配布物に含まれます。実行時のnpmインストールはありません。既存profileでは監視は初期状態で無効です。通常のKiokukoはNodeなしで動作します。パッケージ更新後はHermesを再起動してpluginを読み込み直してください。

**雑談を含む、認証済みの全完了ターンで追加AI呼び出しが発生します。** 有用な経験がなければ保存しません。抽出は非同期・profileごとに同時1件です。長い入力は分割しますが、抽出全体の待機上限は12秒です。時間切れや記録欠落を成功扱いにしません。

抽出先はHermesの`auxiliary.compression`設定です。providerが未指定・`auto`なら、明示された主モデルの`model.provider`と`model.default`（`model.model`も対応）を使います。OpenAI clientとHermesのCodex Responses adapterに対応します。CodexではHermesのOAuth認証・要求変換・stream集約を使い、終了イベントのoutputがnullの場合も扱います。完了イベントのない途中切断は成功扱いしません。具体的な経路が決まらなければ`EXPERIENCE_MODEL_UNCONFIGURED`、未対応のclientなら`EXPERIENCE_ROUTE_UNSUPPORTED`になります。別providerへの自動fallback、SDK再試行、モデル名の固定、APIキーの同梱はありません。

`openai-codex`では`model.api_mode`が未指定ならHermesがResponsesを選びます。監視のためにChat Completionsへ変更する必要はありません。未対応の監視方式は、不完全runの終了時に原因を`error_code`へ保存し、runごとに1回statusへ記録します。過去の不完全traceは`retry`では復元できません。修正版のインストールとHermes再起動後、新しいターンで確認してください。

## 記憶として扱う範囲

ユーザーターンごとに別runを作り、要求、集約済み応答、tool実行結果、usage、エラー、所要時間を記録します。新しいユーザー入力・応答・tool結果だけを経験抽出へ渡します。要求中の過去履歴、system prompt、Kiokuko toolの検索結果は抽出元から外します。モデルの発言だけを独立した根拠にはしません。

経験は状況、観測の要約、対応の要約、検査結果、推論、短い引用を持ちます。引用元のtool結果に終了コードがある場合だけ、その検査の成功・失敗を扱います。モデルの「直った」は成功の証明になりません。要約は未検証であり、引用一致も人間の承認にはなりません。恒久的な指示や人物属性は既存の候補・承認経路に残します。分類やsecret検出は保守的なフィルターであり、完全な正確性や秘匿の保証ではありません。

CLI・DMは本人とworkspace、groupは会話とworkspaceの境界で保存します。workspaceがなければ本人または会話だけに限定します。profile全体には自動共有しません。承認済み・ファイル検証済み記憶を優先し、経験は「未検証の過去事例」として最大2件・800文字、既存の総上限2,200文字内で注入します。

経験の有効期間は90日です。同じscopeの独立した新しい観測は期間を延長できます。再検索や記憶の再掲だけでは確信度や期間を増やしません。

rewindでは、旧世代の記録に由来する未検証の経験を失効させ、抽出jobと再注入を止めます。hostから正確な残存イベント位置が渡されないため、対象世代の経験を保守的にまとめて失効させます。人間が訂正・承認した記憶にはこの自動失効を適用しません。

## 確認・停止・再試行

```sh
hermes kiokuko monitor runs
hermes kiokuko monitor show RUN_ID
hermes kiokuko monitor retry RUN_ID
hermes kiokuko monitor disable
```

statusでは有効化、Nodeの準備状態、runの取得状態、抽出job、元記録の欠落、直近の抽出成功を区別します。取得0件は監視成功の証拠になりません。retryは完全な管理対象runの失敗した抽出を再試行し、APIやtoolは再実行しません。管理操作はローカルCLI専用です。監視の停止中は経験の自動検索も停止し、承認済み・検証済み記憶の利用は継続します。

queueは128イベント・16 MiB、1イベント本文は1 MiBまでです。超過、Node障害、本人情報の不一致では欠落を明示します。記録失敗でもHermesの元の応答・例外・実行回数を維持します。Nodeとの通信は子プロセスのpipeで行い、ネットワーク待受を作りません。

## 保持・削除・backup

保存先は`$HERMES_HOME/kiokuko/traces/`です。directoryは0700、fileは0600とし、認証headerを除外した上でOrcaの保存前redactionを通します。本文記録は機微情報として扱ってください。

traceは最大7日、profile全体で最大1 GiBです。古い確定済み・不完全runから削除します。進行中の記録が容量内に収まらない場合は欠落扱いにして会話を継続します。未抽出の元記録が消えたjobは抽出不能として残します。

```sh
hermes kiokuko monitor purge RUN_ID
hermes kiokuko purge MEMORY_ID
```

前者は元traceを削除して抽出jobを止め、生成済みの記憶は残します。後者は対象記憶と根拠を削除し、その元runのjobを止めます。本文を含まない処理receiptを残し、再処理による復活を防ぎます。Hermesの履歴や既存backup/exportは別です。

backupは記憶DBと根拠の管理情報を含み、trace本文は含みません。restore後に元traceがなければ欠落と表示します。元データを捏造したり外部から取得したりしません。

固定ソースからのbundle再生成とテスト手順は[英語版の開発手順](monitoring.md#development)を参照してください。localhostの固定応答による検証と、実モデルの抽出品質・Telegram/Discordの実配送の検証は別です。
