# jigane.flaky

テストのファイルから、不安定になりやすい書き方（flaky の型）を静的に探します。テストのパスとファイルの内容を受け取り、型の ID と行と理由を返します。LLM は使いません。検出の対象は目録の `catalog.toml` が持つ型で、言語ごとの検出は `queries/<言語>.toml` の tree-sitter の query が持ちます。実行して不安定さを測ることはしません。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `catalog.load_catalog` | `catalog.toml` と `queries/*.toml` を読み込んで検証し、`Item` の一覧に変換します |
| `detector.detect` | パスの一覧からテストのファイルを選別して検出します（機能レビュー式はファイル全体、差分レビュー式は変えた行だけ） |
| `detector.detect_source` | 一つのファイルを検出します（`jigane: allow-flaky` の注記による除外も扱います） |
| `checks.CHECKS` | query の結果に掛ける検査を、名前から関数への対応で持ちます |
| `target.resolve` | 通信の宛先がローカルか、ローカルの外か、確かめられないかを、同じファイルの中で判定します |
| `gates.update_gates` | 検出をやり直し、番号を割り当てて、`gates.json` に保存します |
| `gates.load_gates` | 保存した `gates.json` を読み込みます |
| `finding.to_finding` | 検出一件を区分と説明つきの `Finding` にします。`jigane flaky` の終了コードと、テキストと JSON の出力は、ここから引きます |
| `explanation.explain` | ゲート違反の説明（懸念、理由、直す向き）の三つの文を、目録の項目から組み立てます。返信の文面とレポートはここ以外で文を組みません |
| `gates_v1.upgrade_v1` | 版 1（改名の前）の `gates.json` を版 2 の形へ読み替えます。読み替えの置き場はここだけです |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`gates`、`git`、`judgment_map`、`languages`、`preconditions`、`regex`、`storage`

読まれるサブパッケージ：`commands`、`reviews`

<!-- END GENERATED: dependencies -->

- `languages` はアダプタ、構文木、テストのパス判定に、`git.tree_files` はファイルの読み取りに、`judgment_map.classification` は差分の行の範囲に、`gates` は所見の型と行の範囲に、`regex.gate_tags` は札に使います。
- `reviews.lifecycle` は、レビューの作成と更新で `update_gates` を呼び、画面に出す目録を `load_catalog` で読みます。`commands.failures` は `CatalogError` を処理します。

## Q&A

### なぜゲートの結果を違反（G）と警告（W）に分けるのですか

確度が `direct` の型は違反に、`proxy` の型と宛先を確かめられない通信は警告になり、番号は再利用しません。`proxy` の型や宛先を判定できない通信は、当たっても揺れが起きるとは限らず、違反にすると正しいテストにまで除外の注記を強いるためです。

### なぜ query は検出した節を `@hit` に取り、目録に無い id とアダプタに無い言語を読み込みのときに弾くのですか

query は検出した節を `@hit` に取り、ID が目録に無い query と、アダプタに無い言語の queries ファイルは、`load_catalog` が `CatalogError` にします。読み込みのときに弾くのは、目録と食い違う query を黙って読み飛ばすと、その型の検出が抜けたことに気づけないためです。

### なぜ型を足すときに、`catalog.toml`、`queries/<言語>.toml`、`checks.py` に分けて書くのですか

型を足すときは、`catalog.toml` に項目を、`queries/<言語>.toml` に query を足し、query の結果を絞る検査は `checks.py` に関数を足して `CHECKS` に登録し、query の `check` に名前を書きます。query を言語ごとの別のファイルに分けるのは、複数の作業が並行で足しても同じ項目の中で衝突しないようにするためです。

### なぜ同じ言語で同じ ID の query を複数置けるのは、`check` が違うときだけなのですか

`check` が違うときだけ、同じ言語で同じ ID の query を複数置けます。`check` が違えば当てる書き方と直す向きが別になり（Python の FLK-FLOAT は、pytest の `assert` の比較と `assertEqual` を別の query と検査で当てます）、`id` と `check` の組が同じ query は二重の定義として読み込みのときに弾くためです。

### なぜ検出を許す注記に、理由を必須とするのですか

検出を許す注記は `jigane: allow-flaky <ID> -- <理由>` で、理由が無い注記と、知らない ID の注記は違反になり、注記を読む部品は `jigane.judgment_map.allow_annotation` で、正規表現の `allow-regex` と共有します。除外を行単位で理由つきに限るのは、ファイル単位や設定での一括の除外を許すと、正当でない用例まで一緒に通ってしまうためです。

### なぜ型や言語を足したら、悪い例と良い例のペアも足すのですか

`tests/test_flaky.py` が `tests/fixtures/flaky/<言語>/<型>-bad.txt` と `-good.txt` を使い、悪い例の検出と良い例の無検出を守るので、型か言語を足したらこのペアも足します。検出器は既製のリンタに頼らず自前の query で書くため、項目ごとに、検出すべき例と検出してはならない例で検査する必要があるからです。

## 共通のセットアップの認識

Python の FLK-TIME と FLK-RANDOM は、テストファイルと同じディレクトリから木の根までの各階層の `conftest.py`、およびテストファイル自身の `@pytest.fixture(autouse=True)` の本体を読みます（`setup.py`）。本体で時刻か乱数の種を固定していれば、その当たりは違反でなく警告にし、理由に固定の種類と、見た場所のパスと行を書きます。`ContextCheck` はこの情報（`FileContext`）を第三引数で受ける検査で、`CONTEXT_CHECKS` に登録します。

- 時刻の固定は、本体の `freeze_time(`、`time_machine.travel(`、および第一引数が字面で `datetime` か `time` を指す `monkeypatch.setattr(` です。第一引数は、識別子、属性、文字列（`"time.time"`、`<module>.datetime` など）をドットで区切った語で見ます。`time.sleep` の差し替えは固定とみなしません。
- 種の固定は、本体の `random.seed(` と、第一引数が字面で `random` を指す `monkeypatch.setattr(` です。
- 字面で判断できない第一引数は認めません。
- 語がファイルにあるだけでは認めません。`autouse=True` の明示と、その本体の中の呼び出しの両方を要求します。
- シンボリックリンクと木の外の `conftest.py` は読みません。兄弟のディレクトリの `conftest.py` は効きません。
- FLK-RANDOM は、autouse の fixture の外にある同じファイルの `random.seed(` を、従来どおり検出しません。
- FLK-TIME は、当たりの呼び出しが `with freeze_time(...)`、`with time_machine.travel(...)`、`with travel(...)` の本体の内側にあるとき、または `@freeze_time(...)`、`@time_machine.travel(...)` で修飾した関数かクラスの内側にあるときに、検出しません。
- autouse でない fixture による固定は認めません。
- conftest を実行しないので、固定が実際に効くかは確かめません。だから警告であり、検出の取り下げではありません。
- 差分レビュー式は変えた行だけを見ます。PR が conftest の固定を消しても、変えていないテストの当たりは再評価されません。
