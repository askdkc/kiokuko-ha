# 記憶の検証とcuration

## 自動保存の対象

compact前と会話終了時に、完了したターンから短い確認項目を抽出します。モデルは確認項目の抽出だけを担当し、採否はプロジェクト内の実ファイルを読み直して判定します。

| 対象 | 確認内容 | 保存する意味 |
|---|---|---|
| JSON / TOML | キー・配列index・値・型の一致 | その設定ファイルの指定値 |
| テキストの一行 | 行番号と行全体の完全一致 | そのファイルにその記載があること |
| 推論・原因説明・一般的な教訓 | この検証器では証明できない | 自動採用しない |

例えば設定ファイルにport=8000があれば「設定値は8000」を保存できます。「サーバーが8000番で稼働中」とは判定できません。READMEに書かれた説明も、実際の挙動の証明とは扱いません。保存文は検証器が組み立て、モデルの自由文をそのまま採用しません。

抽出モデルが全項目を拾うこと、自然文のすべての真偽、将来も同じ値であることは保証しません。照合時点のファイル内容の観測です。ファイルシステムとSQLiteは一つのtransactionではないため、最終照合直後の外部変更まで固定できません。検索時にも根拠を再確認します。

## 実行経路

- **compact前**：`on_pre_compress(messages)`で、署名済み`api_content`から元のsnapshotとCWDを特定します。完了receiptのある同じ利用者・会話・workspaceのターンだけを抽出対象にします。保存後にHermesのcompactが中止されても、確認済みのファイル観測は残ります。
- **compact後**：対応Hermes版には完成した要約をproviderへ直接渡すhookがありません。次のcompleted turn同期で、hostの`_compressed_summary`付き履歴を同じ検証器に通します。通常ターンを待たずに要約全体を検証する機能ではありません。
- **会話終了時**：`on_session_end(messages)`から同じ処理を実行します。プロジェクト終了とは、hostが通知するsession境界です。プロセス強制終了やhook未実行時の保存は保証しません。

どの経路も署名marker・workspaceの固定rootがなければ省略し、statusへ理由を記録します。古いgeneration、本人不明、cron、background review、delegationでは自動採用しません。現在のmutableなsessionやCWDへ付け替えません。

入力は最大32,000文字・2,000 messages、抽出結果は最大24項目です。超過時は黙って切り詰めず省略します。抽出にはHermesの`auxiliary.compression`を使うため、その設定先への追加モデル呼び出し・料金が発生し得ます。入力は検査後に送信し、会話全文・要約全文・モデル応答はKiokuko DBへ保存しません。外部モデル提供者側のログはKiokukoの管理対象外です。

抽出待機は最大12秒です。タイムアウト後のworkerにはDB書込処理がなく、遅延した応答を後から採用しません。同時抽出はprocess内で1件に制限し、競合・タイムアウト・拒否は`hermes kiokuko status`へ記録します。

ファイルはsnapshotに固定したCWDからの相対パスで指定し、そのパスが対象会話にも存在することを要求します。走査・コマンド実行・Web検索は行いません。外部パス、symlink、隠しパス、特殊ファイル、128 KiB超のファイル、secret・injection検出内容を拒否します。対応拡張子はjson/toml/md/txt/py/js/ts/yaml/yml/ini/cfgです。YAML等は一行の記載確認だけに対応します。

## 分離と優先順位

通常CLI・DMでは`principal_workspace`、groupでは`conversation_workspace`へ保存します。検証したことを理由に他の利用者・会話へ自動共有しません。プロジェクトが同じでも、本人用記憶がgroupへ混入することはありません。

`file_verified`と確認根拠を別に記録し、人間の承認を偽装しません。検索は関連度とscopeに加えて、現在も成立する検証済みプロジェクト記憶を加点します。現在のユーザー指示より強い命令にはしません。成立しなくなった記憶は検索から除外し、通常hookで期限切れにして既出revisionの無効化通知を返します。

## kioku-curation

### 会話内のslash command（v0.1.1以降）

対象プロジェクトで起動したHermesの対話CLIから操作できます。stdin入力やLLM経由の承認は使いません。

```text
/kioku-curation                 根拠を再確認して候補を表示
/kioku-curation select 1 3      番号のチェックを切替
/kioku-curation all             全選択
/kioku-curation none            選択解除
/kioku-curation show            現在の選択を表示
/kioku-curation share           選択した本文と共有先を表示
/kioku-curation confirm CODE    表示された確認コードで確定
/kioku-curation cancel          共有せず終了
/kioku-curation help            操作一覧
```

`[ ]`と`[x]`は文字によるチェック表示です。各操作をslash commandとして送ります。`share`に表示された本文と共有先を確認し、その画面のコードを`CODE`に指定してください。番号だけ・`yes`だけの通常メッセージでは採用しません。選択変更や再度の`share`は以前のコードを無効にします。

候補はprocess内だけで保持し、表示から15分で期限切れになります。session・generation・profile・workspaceが変わった場合、再起動した場合にも一覧からやり直します。承認時に根拠とrevisionを再検証し、変更や削除があれば全件中止して一覧の再確認を案内します。DBがbusyなら選択を保持し、`share`から再試行できます。再表示・選択・共有の結果は各コマンドの応答で返します。

共有は既存の管理者CLIと同じ権限です。GatewayのDM・groupでは候補を開示せず端末操作を案内します。固定版Hermesのslash handlerには引数文字列しか渡らず、Gatewayでは本人情報のbind前に呼ばれるためです。対話CLIはhostのprofile別PluginManagerに接続されたCLI参照とsession IDを確認します。稼働中のagent、delegation、background review、cron、本人を確認できない経路では実行しません。非同期event loop内の呼出しも拒否します。この内部host契約が変わった場合は再検証が必要です。

### 端末コマンド

インストールを更新した同じPython環境で、対象プロジェクトのディレクトリから起動します。

```sh
kioku-curation
# 同じ操作
hermes kioku-curation
hermes kiokuko curation
python -m hermes_kiokuko curation
```

現在のworkspaceのactiveな検証済み記憶を全件再確認し、確認できた項目だけを候補として表示します。設定が変わった項目は採用対象外になります。チェックは初期状態ですべて未選択です。

```text
Global候補（このHermes profileの全利用者・全プロジェクトに共有）
[ ] 1. プロジェクト ws_… に関する観測: 設定ファイル config.json の ["language"] は "ja"。
    出典: config.json / 元の範囲: principal_workspace / 所有者: profile-owner
    記憶: mem_… revision 1
選択: 0 / 1 件
番号でチェック切替（例: 1 3） / a: 全選択 / n: 選択解除 / s: 選択分を共有 / q: 終了 >
```

番号をEnterで送ると選択が切り替わります。`s`で共有内容を確認し、`share`と入力すると確定します。確認画面でEnterを押すと選択を維持して戻ります。`q`・Ctrl-C・入力終了は共有せず終了します。端末の通常の行入力を使い、色やカーソル位置に依存しません。

Globalは同じHermes profile内の全利用者・全プロジェクトへの共有です。他のprofileのDBへは書き込みません。特定プロジェクトの観測を普遍的な規則に言い換えず、出典workspaceを付けた文面で表示・採用します。一般的な教訓への書き換えと事実検証は別の機能です。

選択した項目は単一transactionで採用します。表示後に本文・revision・根拠ファイルが変わると全件中止します。DBがbusyの場合は選択を維持して再試行できます。根拠が変わった場合は候補を更新し、引き続き有効な項目の選択を維持して再表示します。更新後の本文を再確認してから共有します。重複実行で同じGlobal記憶は作成しません。

Globalには独立したentryを作り、元のプロジェクト記憶を保持します。Global記憶も元ファイルで再検証し、根拠を読めなければ自動注入しません。片方のpurgeはもう片方の明示採用済みentryを消しません。両方消す場合はそれぞれpurgeしてください。

## 設定とDB

自動検証を止めるには`$HERMES_HOME/kiokuko/config.yaml`に設定します。

```yaml
verified_compaction:
  enabled: false
```

DBは従来どおり`$HERMES_HOME/kiokuko/kiokuko.db`です。schema v2は検証根拠・snapshotの固定root・重複排除receiptを追加します。既存v1のchecksum・schema・整合性を検査してからtransactionで移行します。未知schemaや不一致をreset・暗黙修復しません。更新時はagentを終了してpackageを再インストールし、再起動してください。

UIの設計では`kiokuko-ui-design-soul`の`ui.forms.v1`（選択・入力エラー）、`ui.accessibility.v1`（通常のキーボード行入力）、`ui.safety.v1`（共有先の明示と変更後の再確認）を適用しています。
