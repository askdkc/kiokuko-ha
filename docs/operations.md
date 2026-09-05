# 運用と境界

## 設定と互換性

一般pluginのentry pointはmoduleを公開し、MemoryProvider側はregister関数を公開します。Hermes 0.21の二つのloaderが受理する形が異なるためです。

```yaml
memory:
  provider: kiokuko
  memory_enabled: false
  user_profile_enabled: false
compression:
  checkpoint_required: false
plugins:
  enabled:
    - kiokuko-tools
```

`setup`は既存の無関係な設定を保持します。Kiokuko設定は`$HERMES_HOME/kiokuko/config.yaml`に置きます。モデル提案の自動承認、secret検査の無効化、semantic検索は有効化できません。compact時のファイル検証による自動保存は[別の検証経路](curation.md)です。不明な設定、hostの非対応版、checksum不一致、異常DBでは操作を拒否します。

`hermes memory setup`からは通常記憶の自動注入とpassive候補作成を切り替えられます。通常記憶の注入を止めても、既出記憶の訂正通知は維持します。

## Scope

- DMと通常CLIは本人用scopeと現在の会話・workspaceだけを参照します。
- groupでは個人用記憶を検索・注入しません。同じprofileの別利用者をprofile ownerとして扱いません。
- `principal_workspace`はそのturnでworkspaceを確定できる場合だけ使えます。途中のCWD変更は次のsnapshotへ反映します。
- profile/workspace共有は管理者の`share`操作、または`kioku-curation`でのGlobal採用が必要です。共有前の個人用revisionは、権限のない利用者へhistory経由でも返しません。
- 同じremoteを持つ別cloneは既定で別workspaceです。repository内のID宣言だけでは共有を許可しません。管理者が`workspace-link PATH --workspace-id ID`を確認した場合だけ、将来のsnapshotを既存workspaceへ対応づけます。

```sh
hermes kiokuko principals
hermes kiokuko workspaces
hermes kiokuko share ENTRY_ID --expected-revision N --scope profile
hermes kiokuko share ENTRY_ID --expected-revision N --scope workspace
```

CLIはprofileを管理するOS利用者の管理用入口です。CLIの確認は表示内容への承認であり、同じOS権限で任意のPythonやSQLを実行できる攻撃者を隔離する認証機構ではありません。モデルに管理CLIやDBへの無制限なterminalアクセスを渡した構成は、この承認境界の保証対象外です。モデル用の三つのKiokuko toolsには承認・purge機能を公開しません。

## 原文保存と候補

会話の定型入力とCLIの`remember/correct/forget`は同じserviceを使います。原文の空白・改行・Unicodeを黙って書き換えません。secretやinjectionを検知した本文は拒否します。検査は全入力に対する検出保証でも、claimの真実性の証明でもありません。

```sh
hermes kiokuko remember --scope principal --text '返答は日本語にする。' --operation-id human-operation-001
hermes kiokuko correct ENTRY_ID --expected-revision N --text '返答は英語にする。'
hermes kiokuko forget ENTRY_ID --expected-revision N
```

同じCLI操作をretryする場合は同じ`--operation-id`を指定します。同じIDで別内容を渡すと競合になります。会話側ではhostのprofile/session/turnが重複排除の単位です。commit済みの明示操作は後続LLMが中断されても残ります。

`kiokuko_propose`は常にpendingです。`evidence_quote`が元のユーザー本文に含まれていても、否定反転や無関係なclaimをactiveにしません。引用確認はcompleted同期時の元本文で行い、承認とは別に表示します。

passive captureはcompleted turnの短い原文抜粋だけを候補化します。会話全体、tool dump、review harnessを保存しません。background reviewとdelegationの自動capture、embedding jobの投入・consumerはoptional phaseとして未提供です。cronは確定した会話scopeの候補だけを扱い、本人を推測しません。

## 訂正と履歴

一般hookがsnapshot、定型操作、訂正、検索、署名markerの順に処理します。providerのprefetchは空文字列、queue_prefetchはno-opです。

`prepared`はcontextを生成した状態です。後続hookかcompleted同期で、署名・本文digest・元user rowを確認したときだけ`observed_in_history`になります。toolで手動取得した記憶も、提示した可能性があるものとして訂正対象へ記録しますが、履歴確認を偽装しません。

branch/圧縮の親関係はhostの明示IDだけから継承します。markerが圧縮で消えた場合、必要な無効化情報を再提示できます。大量の訂正は、過去のKiokuko context全体を無効とする短い通知へまとめます。

`codex_app_server`とMoAは自動注入対象外です。hook timeout/skip時の配信、LLMが必ず訂正に従うこと、過去のHermes履歴の消去は保証しません。

## Purgeとbackup

```sh
hermes kiokuko purge ENTRY_ID
hermes kiokuko purge-candidate CANDIDATE_ID
hermes kiokuko backup /path/to/new-backup-directory
hermes kiokuko restore /path/to/backup-directory
hermes kiokuko verify
hermes kiokuko reindex
```

purgeは単一transactionでentry、そのrevision、evidence、昇格済み・更新対象candidate、FTS/n-gram/vector、feedback、jobの所有参照を削除します。後継entryは保持します。本文のないtombstoneと重複排除receipt、snapshot等の非本文metadataは残ります。遅延したprojection処理はleaseと対象revisionを再検証し、削除済みデータを再作成しません。

backupはSQLite backup APIでDBとprofile keyを一組にし、manifestとdigestを記録します。既存の出力先は上書きしません。restoreは同じprofileに限り、稼働holderや未処理のlive WALがあれば拒否します。DBの破損やchecksum不一致をresetで隠しません。

`import-native-user-profile PATH --principal PRINCIPAL_ID`は既知の内部principal IDを必要とします。対象と内容量を確認し、検査済みの短い候補としてimportします。自動昇格しません。

## パッケージ更新（v0.1.1以降）

対話CLIの`/kiokuko-update`で更新を開始し、`/kiokuko-update status`で結果を確認します。開始時点の`sys.executable`とprofileの絶対パスを固定し、子processへ`HERMES_HOME`を渡します。対象パッケージは`hermes-kiokuko`、取得元はPyPI、配布形式はwheelに限定します。環境変数やpip設定による別prefix・別indexへの変更は引き継ぎません。独自indexを必要とする環境では端末から更新してください。

同じPython環境を使うprofileは同じ更新を受けます。更新処理は設定・記憶DBを変更しません。対象はvenv内に限定し、pipが必要です。pip未導入なら、そのvenvのPythonで`-m ensurepip --upgrade`を実行してから再試行してください。system Pythonの制約を解除するオプションは使いません。

process内の重複開始を防ぎ、venv直下の`.kiokuko-update.lock`で別processの同コマンドとも排他します。手動pipや他の更新ツールとの排他は保証しません。インストールは最大180秒、インストール済みversion確認は最大15秒です。処理中に通常終了するとworkerの終了を待つため、statusで完了を確認してから終了してください。強制終了・失敗・timeout時の自動rollbackはなく、部分的な更新の可能性を表示します。`retry`は失敗後だけ再実行し、成功後は再起動を案内します。生のpip出力は会話やDBへ保存しません。詳細調査は同じPythonで端末からpipを実行します。

新規sessionはGatewayプロセス内で作られ、読み込んだPython moduleやplugin登録が残ります。Telegram・Discordでも新しい会話だけでは更新が反映されないため、その接続を担当するGatewayプロセスを再起動してください。OSの再起動は不要です。チャット経由の更新権限を確認できないため、このコマンド自体はGatewayからの実行を拒否します。
