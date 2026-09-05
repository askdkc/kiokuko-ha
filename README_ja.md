# Kiokuko(記憶庫) for Hermes Agent

[English README](README.md)

Kiokuko(記憶庫)はHermes Agent用の記憶pluginです。本人・会話・workspaceごとに記憶を分離し、モデルが提案した内容は人間が承認するまで候補として保持します。

compact時と会話終了時には、プロジェクト内のファイルや設定で再確認できる項目だけを検証済み記憶として保存します。保存先は`$HERMES_HOME/kiokuko/kiokuko.db`です。

対応環境はPython 3.11〜3.13、Hermes 0.21系列です。

## インストール

Hermesと同じPython環境へPyPIからインストールします。標準インストールの例です。

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m pip install --upgrade hermes-kiokuko
```

profileを指定して初期化します。

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
export HERMES_HOME="$HOME/.hermes"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

`doctor`で`ok: true`が表示されたら、Hermesを再起動してください。nativeの`MEMORY.md`と`USER.md`は無効になりますが、既存ファイルは削除されません。

## profileが違う場合

`active profile is 'main'` と表示されながら、`Falling back to .../.hermes` と警告された場合、対象profileを明示してから再実行します。

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
export HERMES_HOME="$HOME/.hermes/profiles/main"
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

`HERMES_HOME`はprofileの境界です。Hermesを起動するprofileと同じ値を使ってください。profileごとに設定、DB、セッションが分かれます。

## 更新

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m pip install --upgrade hermes-kiokuko
"$HERMES_PY" -m hermes_kiokuko doctor
```

更新後はHermesを再起動します。Python 3.14は現在の対応範囲外です。

## 明示保存と承認

メッセージ全体を次の形式にすると、原文を即時保存できます。本文は600文字までです。

```text
@kiokuko remember --scope principal
返答は日本語にする。
```

モデルが提案した記憶や自由な自然文の「覚えて」は候補になります。候補の確認と承認はCLIで行います。

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m hermes_kiokuko pending
"$HERMES_PY" -m hermes_kiokuko approve CANDIDATE_ID
```

`kioku-curation`では、検証済みのプロジェクト記憶を再確認し、同じprofile内のGlobal記憶へ共有する項目を選べます。

```sh
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -m hermes_kiokuko curation
# venvのbinディレクトリがPATHにある場合:
kioku-curation
```

詳細は[記憶の検証とcuration](docs/curation.md)、[運用・境界](docs/operations.md)、[テストの実行](docs/verification.md)を参照してください。
