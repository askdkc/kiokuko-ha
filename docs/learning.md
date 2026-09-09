# 経験抽出と教訓の学習

この機能はMemmyのepisode／evolution周辺の考え方を、既存のOrca記録・SQLite・scope・revisionへ選択導入したものです。教訓は**過去経験からの推論**であり、検証済み事実やユーザー指示には昇格しません。

## 設定と導入

既存・新規profileとも学習の既定値は`off`です。`off`でも監視を有効にすれば経験抽出は動きます。`shadow`は教訓を生成・更新して保存しますが会話へ渡しません。`off`／`shadow`中はモデルの`get/history/conflicts`経路からも教訓を除外し、調査には端末の`show/history`を使います。`auto`は監視が有効で、根拠と適用条件を満たす教訓だけを注入します。設定変更はローカル管理者向けの端末操作です。

Hermesを導入した環境で、このパッケージを更新し、Hermesを再起動してから実行します。

```sh
hermes kiokuko monitor enable
hermes kiokuko monitor status
hermes kiokuko learning shadow
hermes kiokuko learning status
```

実モデルの評価基準を満たした後の切り替えと停止:

```sh
hermes kiokuko learning auto
hermes kiokuko learning status
hermes kiokuko learning off
hermes kiokuko learning status
```

対応するKiokuko設定は`$HERMES_HOME/kiokuko/config.yaml`の次の値です。Hermes本体のモデル設定とは別です。

```yaml
experience_learning:
  mode: off  # off | shadow | auto
```

`auto`コマンドは評価を実施・認証しません。実モデルによる評価が済んでいない環境では`off`か`shadow`を使います。今回のcheckoutには評価データと実行手順を用意していますが、実運用Hermesと実モデルによる品質評価・有効化は実施していません。

## 抽出と根拠

- `observation`とtool名でcall/resultを対応付けます。モデルは連続する作業区間、経験、試行、条件とフィールド別引用を一度に提案します。
- 入力はJSON表現で最大32,000文字、1 run最大4窓。直前のcall/result単位を重ねます。巨大イベントは絶対文字位置付きで分割し、巨大な並列実行単位は断片へ分割します。5窓目以降は処理せず、対象外の`seq`・文字範囲を保存します。
- 窓ごとに検証済み提案を保存して再開します。全対象窓の処理後、回復を確認した経験、結果付き経験、結果不明の順で最大3件を選び、同順位では異なる作業を優先します。
- 数値終了コードはその呼び出しの終了結果だけを示します。回復には同じtool・コマンド・実行引数の先行失敗と後続成功が必要です。別コマンド・別cwd・文字列やboolの終了コードでは回復を認定しません。先行失敗と各試行は残します。
- 複数runの回復は、同じsession・generation・scope・検証対象での順序付き関係として保存します。本文を合成して成功へ書き換えません。
- 完全重複はscopeと構造化内容のreceiptで排除します。関連経験はtool・対象・実行引数・条件のフィールド別特徴で結び、全文Jaccardによる本文統合は行いません。同じsessionの再試行や言い換えは支持数を増やしません。

初期実装の結果検証は`command`／`cmd`を持つtool呼び出しと、整数の`exit_code`／`exitCode`／`returncode`に限定しています。自由文による成功報告や、コマンドが異なる検査同士の意味的な同一性は認定しません。既存形式の経験は読めますが、構造化根拠のない旧経験は教訓の支持に使いません。

## 教訓の更新と利用条件

同一profile内のscope・所有者・workspaceとフィールド別特徴が一致する経験を、最大20件・32,000文字まで一回の生成に渡します。上限超過は`LEARNING_EVIDENCE_LIMIT`または`LEARNING_INPUT_LIMIT`として停止します。都合のよい20件を選んで反例を落としません。groupの閲覧範囲は従来の会話scopeを保ちますが、異なる参加者の観測を一つの教訓の支持へ合算しません。類義語による広い候補統合は行わないため、表記違いによって学習が進まないことがあります。

モデルは適用条件、対応候補、避ける対応、検証方法、適用外、支持ID、反例IDを返します。内容は元経験の許可されたフィールド値からの引用に限定します。未知のID、別scope、未観測のコマンド、一般的な「注意する」は拒否します。未検証の代替策は作りません。

`candidate → active`には異なる3 session以上の支持が必要です。推奨には2 session以上の対応付き検証成功、回避には3 session以上の一致条件での失敗を要求します。現実装は各支持に同じ対応・検証を求めるため、推奨も実質3 session以上の成功が必要です。generation変更、検索・注入回数、feedback、モデルの自己評価、unknownは成功支持を増やしません。この閾値は校正済み確率ではありません。

新しい根拠の追加時は既存教訓を先に`contested`へ移し、再計算まで利用を止めます。生成時には適用条件内の反例を必須にします。条件を狭めて除外するには、引用されたOS、バージョン、`key=value`条件などの明示的な不一致が必要です。条件欠落は反例を除外する理由になりません。根拠を持つ教訓を作れなければ`retired`にします。モデル呼び出し失敗では停止状態を維持します。

各改訂は旧本文・支持・反例・理由をrevisionへ残します。人間が訂正・失効などの変更を加えた教訓は自動更新から切り離します。feedbackは再評価のきっかけだけに使います。

検索では`active`かつ現在の根拠が有効な教訓だけを使います。必須条件の文字列が現在の検索文に現れることを要求し、認識できるバージョン・環境の不一致や否定では除外します。自由文の否定・因果関係・暗黙条件の完全な理解を保証するものではありません。

承認済み・検証済み記憶を優先し、経験と教訓を合わせて最大2件・800文字に制限します。教訓とその元経験は重複注入しません。「過去経験からの推論」「適用条件」「検証方法」を表示し、旧revisionは既存の無効化通知で取り消します。検索結果や教訓本文そのものは新しい観測根拠になりません。

## 実行・削除・調査

抽出を教訓更新より先に処理します。1処理単位につきモデル呼び出し1回、待機12秒。既存の補助モデル経路を使い、指定経路から暗黙fallbackしません。タイムアウトした処理は遅延応答を採用せず、呼び出しが実際に終了するまで同じprofileのモデルworkerロックを維持します。

モデル待機中にDB transactionは保持しません。commit時に根拠revision、session generation、scope、設定、job所有権を再照合します。窓進捗、job、receipt、教訓構造をschema v4へ追加しています。v1/v2/v3からの移行とbackup/restoreは既存のchecksum検証を通します。

```sh
hermes kiokuko monitor show RUN_ID
hermes kiokuko monitor retry RUN_ID
hermes kiokuko learning status
hermes kiokuko learning retry
hermes kiokuko show MEMORY_ID
hermes kiokuko history MEMORY_ID
```

`monitor show`の`extraction_coverage`で処理窓数と対象外範囲を確認できます。`learning retry`は失敗jobを再試行可能にします。監視が有効なHermesプロセスのworkerが処理します。`kiokuko_recall`の`get/history`にも教訓の構造、支持・反例、変更理由を追加しています。

通常の7日／容量によるtrace削除では、採用時に照合した最小限の引用・試行を残します。`trace_state=missing`なら本文の再照合はできません。支持は観測時刻から90日を超えると除外され、再生成・再検索では延長しません。期限切れで支持不足になった教訓は注入されません。

```sh
hermes kiokuko monitor purge RUN_ID
hermes kiokuko purge MEMORY_ID
```

明示的purgeでは依存する自動教訓、旧自動revision、保留出力を削除し、本文のないreceiptを残します。元traceのpurgeはその経験も失効させます。人間が訂正した現本文は保持しますが、そこに紐づく旧自動教訓の履歴は削除します。経験の訂正・失効・rewindも教訓を直ちに停止させます。既存のbackupやHermes本体の履歴は別途管理します。

## 回帰試験と実モデル評価

```sh
.venv/bin/python -m pytest tests/integration/test_learning.py tests/integration/test_learning_quality.py -q
.venv/bin/python -m pytest tests/hermes_e2e/test_learning_host.py -q
```

`benchmarks/learning_cases.json`には日本語・英語の固定60ケースを収録しています。40ケースが適用外・結果不明で、10ケースは長いrun後半の回復です。手書きの期待結果と固定モデル応答による試験は、境界・保存・適用判定の回帰試験です。実モデルの精度測定ではありません。Hermes host試験も、固定版Hermesと隔離profile／固定応答を使います。実Telegram配送や有料APIは呼びません。

実モデルが使えるHermes環境での比較:

```sh
.venv/bin/python scripts/evaluate_learning.py \
  --home /absolute/path/to/hermes-profile \
  --output /tmp/kiokuko-learning-review.json
```

追加のモデル呼び出しが発生します。明示したprofileの補助モデル設定を読み、出力以外の実運用DB・設定には書き込みません。旧方式はこのリポジトリの`a99c1de`の抽出prompt・分割・検証を固定しています。同じモデルへ旧方式と新方式を渡し、採用結果、拒否理由、呼び出し数、usageが返る場合の入出力token数、待ち時間を出力します。中断時も完了ケースまで保存します。

新方式の教訓評価は、抽出根拠から作った**合成3 session**による適格性・適用判断の試験です。独立した実運用sessionの成功実績ではありません。教訓の複数経験集約・反例改訂は別の固定応答による回帰試験で確認します。

評価者がJSON内の`review`へ抽出の根拠・関連性・回復保持・重複・適用判断を記入した後、集計します。`lesson_offered`は実行結果なので変更しません。全回帰試験の成功を別途確認してから`boundary_suite_passed`を設定します。

```sh
.venv/bin/python scripts/evaluate_learning.py --score /tmp/kiokuko-learning-review.json
```

60ケースのラベルが揃わなければ採点しません。新方式の根拠なし成功0件、教訓適用precision 95%以上、適用外注入5%以下、長いrunの回復保持が旧方式より改善、抽出precisionが低下しないことを要求します。scope越境・削除後復活0件は回帰試験で別途確認します。未達・未測定なら`auto`へ切り替えません。

## アルゴリズムの参照元

Memmyの固定commit [`98146714aad8569a298cf8692946da8bb28bf7cb`](https://github.com/MemTensor/memmy-agent/tree/98146714aad8569a298cf8692946da8bb28bf7cb)にある`big-turn-span-pipeline.ts`、`span-pipeline.ts`、`negative-experience-pipeline.ts`、`policy-induction.ts`の考え方を参照し、独立に実装しています。L1/L2/L3、world model、Skill生成、ベクトル基盤、reward/gain、profile横断統合は追加していません。
