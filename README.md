# Kiokuko for Hermes Agent

Hermes専用の記憶pluginです。compact時と会話終了時に、プロジェクト内のファイル・設定で確認できた項目を記憶として保存します。検証済みの記憶はプロジェクト内で優先して再利用し、`kioku-curation`で選んだ項目をGlobalへ共有できます。本人・会話ごとの分離は維持します。

通常のモデル提案は承認待ちです。自動採用は実ファイルと照合できる設定値・記載内容に限り、推論や教訓を「事実」として自動確定しません。

Python 3.11–3.13、SQLite、Hermes 0.21系列を対象にしています。統合テストの固定コミットと検証範囲は[検証記録](docs/verification.md)を参照してください。

## 導入

Hermesと同じPython環境で、このcheckoutからインストールします。

```sh
python -m pip install .
python -m hermes_kiokuko setup
hermes kiokuko doctor
```

setupは対象Hermes profileで一般pluginを有効にし、nativeの`MEMORY.md`と`USER.md`を両方無効にします。既存ファイルは変更・削除・自動importしません。設定後はagentを再起動してください。

## 保存と承認

通常のCLI会話、または本人を確認できるDMで、メッセージ全体を次の形式にすると原文を保存します。本文は600文字までです。

```text
@kiokuko remember --scope principal
返答は日本語にする。
```

モデルの提案や自由な自然文の「覚えて」は承認待ちになります。

```sh
hermes kiokuko pending
hermes kiokuko approve CANDIDATE_ID
```

承認画面でclaim・scope・根拠・更新対象revisionを確認します。表示後に内容が変わった場合は承認を拒否します。

```text
@kiokuko correct mem_ID --expected-revision 1
返答は英語にする。
```

```text
@kiokuko forget mem_ID --expected-revision 2
```

訂正・撤回は`continue`などの短い入力でも再提示します。過去のHermes履歴を書き換える機能ではありません。

## 運用

プロジェクトのディレクトリで実行すると、検証済み記憶を再確認して選べます。

```sh
kioku-curation
# または hermes kioku-curation
```

番号でチェックを切り替え、`s`で共有内容を確認、`share`で確定します。Globalは**現在のHermes profileの全利用者・全プロジェクト**への共有です。元のプロジェクト記憶は残ります。

検証できる内容、compact後の処理時点、操作例は[記憶の検証とcuration](docs/curation.md)を参照してください。

```sh
hermes kiokuko status
hermes kiokuko show ENTRY_ID
hermes kiokuko backup /path/to/new-backup-directory
hermes kiokuko purge ENTRY_ID
```

purgeは対象確認後に、稼働DB内の本文・根拠・候補・検索データを削除します。履歴の訂正に必要な最小metadataは残ります。Hermes履歴、既存backup/export、ディスクの物理消去は対象外です。

[運用・境界](docs/operations.md) · [実装仕様](PLAN.md) · [テストの実行](docs/verification.md)
