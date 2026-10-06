# Kiokuko(記憶庫) for Hermes Agent

[English README](README.md)

Kiokuko(記憶庫)はHermes Agent用の記憶pluginです。本人・会話・workspaceごとに記憶を分離し、モデルが提案した内容は人間が承認するまで候補として保持します。

compact時と会話終了時には、プロジェクト内のファイルや設定で再確認できる項目だけを検証済み記憶として保存します。保存先は`$HERMES_HOME/kiokuko/kiokuko.db`です。

Kiokukoの対応範囲はPython 3.11〜3.14、Hermes 0.21系列です。現行PMのソース契約は`88c60858468d7adee27a752242c7c507fa4129d0`で検証します。Python 3.14ではHermes 0.21.4以降が必要です。それ以前のhostは記憶同期に使うスレッド処理が3.14に対応していません。

## 現行HermesのPMで導入する

現行HermesはpluginのコードとPython依存関係をまとめて管理します。このGitリポジトリから直接導入できるため、バージョン付きarchiveの展開や手動コピーは不要です。`hermes`は対象のHermesインストールのランチャーを使ってください。旧`venv/bin/python`へのインストールではPMの管理対象になりません。

CLIやGatewayが使うprofileに合わせて、以下の手順を選んでください。例は標準の`$HOME/.hermes`を使います。独自のrootを使っている場合は、この部分を対象Hermesのrootに置き換えます。`--profile`も明示するため、保存済みのactive profileによってdefault用のコマンドが別profileへ向かうのを防げます。

### defaultプロファイル

defaultの保存先は`$HOME/.hermes`直下です。`profiles/default`ではありません。

```sh
export HERMES_HOME="$HOME/.hermes"
hermes --profile default plugins install askdkc/kiokuko-ha --enable --yes-deps || exit 1
hermes --profile default kiokuko setup || exit 1
hermes --profile default kiokuko doctor --load-plugin || exit 1
```

doctorの`ok: true`と`command_registration.ok: true`を確認してから、このprofileの担当Gatewayを再起動します。

```sh
hermes --profile default gateway restart
```

### mainプロファイル

既存の`main`プロファイルには`$HOME/.hermes/profiles/main`を使います。まだ作成していない場合は、先に`HERMES_HOME="$HOME/.hermes" hermes --profile default profile create main`で作成し、CLIやGatewayの設定を済ませてください。

```sh
export HERMES_HOME="$HOME/.hermes/profiles/main"
hermes --profile main plugins install askdkc/kiokuko-ha --enable --yes-deps || exit 1
hermes --profile main kiokuko setup || exit 1
hermes --profile main kiokuko doctor --load-plugin || exit 1
```

doctorの`ok: true`と`command_registration.ok: true`を確認してから、`main`の担当Gatewayを再起動します。

```sh
hermes --profile main gateway restart
```

どちらのprofileでも、対話CLIは起動し直してpluginを読み込みます。Gatewayは再起動後にチャットの`/commands`を確認してください。端末のdoctor成功だけでは、稼働中Gatewayの読み込みを確認したことにはなりません。`setup`はnativeのMEMORY.md・USER.mdを無効にしますが、既存ファイルやKiokukoの記憶は保持します。

インストール元はGitリポジトリのdefault branchです。Gitを直接指定するため、カタログ掲載は必要ありません。`--yes-deps`は依存関係の導入を明示的に承認する指定です。インストールや依存関係の準備が失敗したら、setupへ進まず先にそのエラーを解消してください。

Git経由で導入済みなら`plugins install`を省き、上の対応する`HERMES_HOME`を指定して、`hermes --profile default plugins update kiokuko-tools`または`hermes --profile main plugins update kiokuko-tools`で更新します。その後、同じprofileのsetup・doctorを実行し、担当Hermesプロセスを再起動してください。手動コピーしたarchiveには更新元のGit情報がないため、Hermesのソース導入手順で置き換える必要があります。native版の`/kiokuko-update`は管理された更新手順を案内し、pipを実行しません。

公式カタログへの掲載は、人間による審査を伴う別の手続きです。現在の掲載・承認は主張していません。旧pip版の自己更新コードも、カタログ規約に沿った別途の確認が必要です。[Hermesのplugin仕様](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/)と[カタログ申請要件](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission/)を参照してください。

### PM 導入：`Unknown command` またはモジュール欠落

enabled は設定状態であり、読み込み成功の証明ではありません。Gateway のログに `No module named 'hermes_kiokuko'` がある場合は、まず Hermes 本体のコマンドで確認します。欠落した Kiokuko コマンド自体では復旧できません。`main` の例です。担当環境のランチャーを使ってください。

```sh
export HERMES_HOME="$HOME/.hermes/profiles/main"
hermes --profile main plugins list
hermes --profile main pm status
hermes --profile main pm doctor
```

Gateway の起動診断にある Python、prefix、profile、plugin の場所、関係する検索パスを PM の選択環境と照合します。同じ Python 実行ファイルでも、読み込む PM 環境は異なる場合があります。古い venv の doctor 成功が証明するのは、その古いプロセスだけです。

PM が記録済み環境の破損を報告する場合は `hermes --profile main pm repair`、追跡された Git 更新には `hermes --profile main plugins update kiokuko-tools` を使います。エラーを解消してから `hermes --profile main kiokuko doctor --load-plugin` を確認し、担当 Gateway を再起動して Discord の `/commands` と `/kiokuko-update status` を確認します。新しい Gateway でも正常な選択環境を読み込めない場合は、起動時の証拠を Hermes の不具合報告に残します。`src` の強制追加や PM 環境への pip 導入は行いません。

`runtime_provenance` は設定 manifest、現在のディスク版、読み込み済みコードの fingerprint・版、distribution metadata、PM 選択を分けて表示します。PM 情報を取得できない場合は確認不能です。端末診断は常にその端末プロセスを記述します。managed 版のチャット status は、ローカルパスを出さず、短いコード識別と再起動の案内を表示します。版番号の一致だけでは更新反映を証明できません。

## 旧Hermes用のpip導入（PMのない環境）

以下は、Pythonのvenvを直接管理する旧Hermes用の手順です。現行PMの管理環境には実行しないでください。


対象Hermesプロセスを実際に起動しているPython環境へインストールします。Gatewayのチャットで使う場合は、そのGatewayの起動コマンドやサービス設定で指定されたPythonを使い、venv内のパスを保持してください。`$HOME/.hermes/hermes-agent/venv/bin/python`を使えるのはGatewayもそのPythonで動く場合だけです。そこへインストールしても、別のPythonで動くGatewayには反映されません。

以下のパスを実際のPythonに置き換え、パスとバージョンを確認してからインストールします。

```sh
export HERMES_PY="/absolute/path/to/gateway/python"
"$HERMES_PY" -c 'import sys; print(sys.executable); print(sys.version)'
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
```

profileを指定して初期化します。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

`doctor`で`ok: true`が表示されたら、Hermesを再起動してください。nativeの`MEMORY.md`と`USER.md`は無効になりますが、既存ファイルは削除されません。

## profileが違う場合

`active profile is 'main'` と表示されながら、`Falling back to .../.hermes` と警告された場合、対象profileを明示してから再実行します。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor
```

`HERMES_HOME`はprofileの境界です。Hermesを起動するprofileと同じ値を使ってください。profileごとに設定、DB、セッションが分かれます。

## 更新

現行PMで導入した場合は、[現行HermesのPMで導入する](#現行hermesのpmで導入する)にあるprofile別の`plugins update`手順を使ってください。

### 旧pip版の更新（PMのない環境）

Kiokukoのコマンド用pluginが読み込まれたHermesの対話CLI・Discord・Telegramなどのチャットから更新できます。

```text
/kiokuko-update
/kiokuko-update status
```

Hermesを実行しているPythonを使い、現在のprofileを`HERMES_HOME`で明示してバックグラウンド更新します。更新対象はPyPIの`hermes-kiokuko`です（リポジトリ名は`kiokuko-ha`）。同じPython環境を共有するprofileには同じパッケージ更新が適用されます。profileの設定・記憶DBは変更しません。失敗時は`/kiokuko-update retry`で再試行できます。GatewayではHermesのコマンド権限設定に従います。

`/kiokuko-update`にはHermesのPython環境内の`pip`が必要です。ない場合は、下の端末手順で`uv`を使ってください。

v0.1.0からの初回更新や、端末から更新する場合：

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main" # 実際のprofileパスに合わせる
cd "$HOME/.hermes/hermes-agent" || exit 1
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --upgrade hermes-kiokuko
else
  "$HERMES_PY" -m pip install --upgrade hermes-kiokuko
fi
"$HERMES_PY" -m hermes_kiokuko doctor
```

更新完了を確認してからHermesプロセスを再起動します。Telegram・Discordで使う場合は、そのチャットを担当するHermes Gatewayプロセスを再起動してください。新しい会話セッションだけではpluginコードが再読込されません。OSの再起動は不要です。サービスでGatewayを管理している場合は、サービスの起動Pythonも確認してください。別のPythonから端末で再起動しても、サービス設定のPythonは変わりません。

### `/kiokuko-update` が `Unknown command` になる場合

そのGatewayにはコマンドが登録されていません。未登録の更新コマンドでは復旧できないため、まず端末で同じPython・profileの設定と登録を確認します。`main`の例です。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1
"$HERMES_PY" -m hermes_kiokuko setup
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
```

`--load-plugin`は設定されたHermes pluginをこの端末プロセスへ読み込み、Kiokukoの4コマンドの登録と所有者を検査します。通常の`doctor`は読み込みを行わず、登録確認は`checked: false`です。いずれも稼働中Gatewayの登録証明にはなりません。`hermes plugins list`の`enabled`も設定状態であり、読み込み成功の証明ではありません。

`command_registration.ok: true`と全体の`ok: true`を確認してから、同じprofileの担当Gatewayを再起動します。

```sh
"$HERMES_PY" -m hermes_cli.main --profile main gateway restart
```

再起動後、チャットの`/commands`に`kiokuko-update`があるか確認します。端末では登録成功でもGatewayにない場合は、担当プロセスのPython・profile・再起動対象を確認してください。登録検査が失敗した場合はGateway起動ログの`Failed to load plugin 'kiokuko-tools'`と診断結果を確認します。`--load-plugin`が未対応の旧版では、上の通常の端末更新を使ってから再検査してください。`pip`の有無は登録後の更新処理の問題であり、`Unknown command`の原因を説明しません。

### `hermes_yaml` が見つからない場合

`hermes_yaml`はHermes本体の`hermes_yaml.py`です。Kiokukoの依存パッケージではありません。Hermes更新前のeditable installが新しいroot moduleを検索できない場合、Kiokukoはインストール済み`hermes_cli`と同じHermesソースを検索パスへ追加します。作業ディレクトリやprofile内のファイルは復旧元に使いません。

それでも失敗する場合は、Hermesを実行するPythonとチェックアウトを確認します。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
cd "$HOME/.hermes/hermes-agent" || exit 1
test -f hermes_yaml.py || { echo 'Hermes source is missing hermes_yaml.py'; exit 1; }
"$HERMES_PY" -c 'import hermes_yaml; from hermes_cli.plugins import get_plugin_commands; print(hermes_yaml.__file__)'
"$HERMES_PY" -m hermes_kiokuko doctor --load-plugin
```

Hermes側でこのモジュールを要求しているのにファイルがない場合は、Hermesソースの欠損・更新不整合を復旧する必要があります。ファイルがあるのに`ruamel.yaml`などの依存関係が欠けている場合も、Hermes自身のインストール手順で復旧してください。Kiokukoは代替YAML実装を注入せず、hostの互換性検査も省略しません。端末で成功したら対象Gatewayを再起動し、チャットでコマンドを確認してください。

### wheelの手動インストールと復旧

**起動のたびに再インストールする必要はありません。** 公開済みのリリースは通常の更新手順を使います。この手順は、受け取ったwheelを入れる場合に使います。未公開の修正版や、同じバージョンの別ビルドを入れ直す場合も含みます。リポジトリ内の未公開変更はPyPIからの更新には入りません。

Hermesが動くマシンへwheelを転送し、`WHEEL`を実際のファイルパスに合わせてください。下の`VERSION`は受け取ったファイル名のバージョンに置き換えます。`HERMES_HOME`は対象profileに合わせます。`--no-deps`は、そのHermes環境にKiokukoの依存パッケージが既に入っている前提です。初回インストールで依存パッケージも必要なら、このオプションを外してください。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
export HERMES_HOME="$HOME/.hermes/profiles/main"
cd "$HOME/.hermes/hermes-agent" || exit 1

WHEEL="$HOME/hermes_kiokuko-VERSION-py3-none-any.whl"

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERMES_PY" --no-deps --reinstall "$WHEEL"
else
  "$HERMES_PY" -m pip install --no-deps --force-reinstall "$WHEEL"
fi

"$HERMES_PY" -c 'from plugins.memory import find_provider_dir; p = find_provider_dir("kiokuko"); print(p); assert p is not None'
"$HERMES_PY" -m hermes_kiokuko doctor
```

初回インストールでは、上の初期化手順と同じく、対象profileへ`setup`してから`doctor`を実行します。ディレクトリの検出成功は、このCLIプロセスでパッケージを検出できたことを示します。稼働Gatewayが読み込んだ証明にはなりません。インストールが成功し、`doctor`で`ok: true`を確認したら、対象profileを担当するHermesプロセスを再起動してください。検出結果が`None`、または`doctor`が失敗する場合は[provider検出とhostの診断](docs/operations.md)を参照してください。

## 明示保存と承認

メッセージ全体を次の形式にすると、原文を即時保存できます。本文は600文字までです。

```text
@kiokuko remember --scope principal
返答は日本語にする。
```

モデルが提案した記憶や自由な自然文の「覚えて」は候補になります。候補の確認と承認はCLIで行います。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko pending
"$HERMES_PY" -m hermes_kiokuko approve CANDIDATE_ID
```

`kioku-curation`では、検証済みのプロジェクト記憶を再確認し、同じprofile内のGlobal記憶へ共有する項目を選べます。

v0.1.1以降は、対象プロジェクトで起動したHermesの対話CLI内から操作できます。

```text
/kioku-curation
/kioku-curation select 1 3
/kioku-curation share
/kioku-curation confirm CODE
```

`CODE`は`share`後に表示される確認コードに置き換えます。`cancel`で共有せず終了します。Global記憶は同じprofileの全利用者・全プロジェクトへ共有されます。GatewayのDM・groupではこの管理操作を実行できないため、ローカルの対話CLIを使ってください。端末コマンドも引き続き利用できます。

```sh
: "${HERMES_PY:?Set HERMES_PY to the Python executable used by the target Hermes process}"
"$HERMES_PY" -m hermes_kiokuko curation
# venvのbinディレクトリがPATHにある場合:
kioku-curation
```

詳細は[記憶の検証とcuration](docs/curation.md)、[運用・境界](docs/operations.md)、[テストの実行](docs/verification.md)を参照してください。

## 任意のAPI監視・経験記憶

HermesのCLI・GatewayからOpenAI互換APIへの要求・応答をOrcaReplayへ記録できます。Node.js 22.12以降を用意し、対象profileのHermes対話CLIまたはチャットで有効化・状態確認します。

```text
/kiokuko-monitor enable
/kiokuko-monitor status
```

停止は`/kiokuko-monitor disable`です。

全完了ターンで最大4窓の追加AI抽出を行い、有用な経験を**未検証の過去事例**として自動検索します。既存の承認済み記憶とは区別します。監視は初期状態で無効、traceは最大7日・profileあたり1 GiBです。[設定・モデル経路・記憶の境界・削除](docs/monitoring.ja.md)を確認してください。複数経験からの教訓生成・改訂・条件付き利用は別設定で、学習の既定値は**off**です。[切り替え手順と品質評価](docs/learning.md)を参照してください。

## 任意の作業プロフィール記憶

完了したCLI・DMの依頼に明示された対象を、追加のモデル呼び出しなしで参照できます。既定はoffです。候補を表示しない収集モードで有効化し、状態を確認します。

```sh
python -m hermes_kiokuko task-profiles mode shadow
python -m hermes_kiokuko task-profiles status
```

[設定・各モード・上限・削除](docs/task-profiles.md)

[出典を検査する調査コマンドと診断](docs/research.md): `/kiokuko-research <request>` / `/kiokuko-research status`.
