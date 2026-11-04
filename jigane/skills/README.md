# jigane.skills

LLM に指摘を作らせるスキル群を持ちます。レビューの群を実行する層です。Claude Code CLI を呼ぶ発見役と、候補を検証する役を組み立て、指摘として正規化して保存します。入力はレビューの対象（`jigane.reviews.lifecycle.Review`）と判断の地図で、出力は `findings` と実行の記録です。CLI の表示は受け持ちません。

## 群

| 内部名 | 表示名 | 入口 | 内容 |
| --- | --- | --- | --- |
| `macro_principles_run` | 方針 | `macro_principles_run.run_policy` | 方針の群を走らせる唯一の入口です。図の形は図の起動に加えて、その原則を問える図の形があるときだけ原則ごとに一本ずつ、最大五本を走らせ、設計書の形は一本です。見積もりは `estimate_policy` で、図の形は実際に組み立てた起動のプロンプトの和で数えます。図の起動は claude-sonnet-5-5 で上限 0.36 USD、原則の起動は claude-haiku-5-5 で 1 本の上限 0.02 USD です |
| `macro` | 方針 | `macro.run_macro` | 変更の道筋の誤りを、図と表だけから外形を問います。`extras` を受け、図の形では図の起動に、形のある原則ごとの起動（`ExtraLaunch`）を足した最大五本を並列に走らせます。どの起動にも道具は渡しません。起動は見直しと範囲の判断まで行い、最終の指摘を返します。検証は行わず、`adjudication` を持ちません |
| `micro` | 実装 | `micro.run_micro`（実行は `sharded.run_sharded`） | 差分をシャードに分け、シャードごとの発見役を並列に起動し、敵対的検証にかけます |
| `security` | セキュリティ | `security.run_security`（実行は `sharded.run_sharded`） | シャードごとの発見役が MCP の参照体系を引き、敵対的検証にかけます。MCP の設定が要ります |

各群に `estimate_*` があり、実行前に費用を見積もります。実装とセキュリティは `sharded.py` の実行器を共有し、見積もりの型と選択は `estimate_result.py`、`estimator.py`、`output_estimate.py` が持ちます。実行の途中経過は `run_record.py` が記録します。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `adversarial.verify` | 候補を、反証役と判定役、または代案役と擁護役と判定役で検証します |
| `findings.triaged` | 型の指摘を一つ受け、区分（`Triage`）と理由の確定した指摘を返します。実行時の付与の唯一の入口で、古い指摘の補い（`fill_triage`）も同じ決定を引きます |
| `triage.triage_micro` | 実装の指摘をテーマに束ね、並べます |
| `order.policy_notice` | 方針が未実行、古い、必読が残るとき、その理由を返します。実装とセキュリティは止めず、実行の前に案内します |
| `shards.assign_shards` | ファイルを単位に、大きさが揃うようシャードへ分けます |
| `input_budget.check_input_budget` | 起動の前に、プロンプトの大きさの下限の見積もりで、1 回の起動の上限（CLI に渡す値は次の一往復の余裕を引く）を超えそうな起動を止めます。全ての群が共有します |
| `pipeline.save_run` | 候補の正規化と、実行の保存を群の間で共有します |
| `group_result.group_outcome` | 群の実行の結果の五状態を決めます |
| `sharded.run_sharded` | シャードに分けた群（実装、セキュリティ）の共通の実行器です。発見役の起動、並列化、失敗の集計、範囲外の除外、検証、保存を持ち、群は `ShardedGroup` で違いだけを渡します |
| `estimator.estimate_of` | 群の識別子から、その群の見積もりの関数を選びます。群ごとの分岐はここだけに置きます |
| `estimate_result` | 見積もりの結果の型です。群ごとの型の和で、確認の画面と `jigane estimate` の出力が読みます |
| `output_estimate.estimate_output` | 設計書の形の見込みの総額を、入力の費用に過去の実額から出した倍率を掛けて返します |
| `run_record.tracking` | 群の実行の途中経過を記録します。割り込みで止まった実行を、止めた実行の記録へ移します |
| `received_concerns.received_concerns` | 実装の群が方針から受け取った懸念を、シャードと指摘に対応づけます |
| `assembly.AssemblyInput` | 資料の組み立てへ渡す唯一の入力です。レビューと、履歴（同じレビューの分と関連するレビューの分）を作る場所で一度だけ読んで束ね、組み立ての中では読み直しません |
| `history.history_text` | 前の回の指摘を、回ごとに主張と根拠の位置の一行ずつにして返します。方針と実装が共有します。`shard_history_text` はシャードの雛形の `$history` に差し込む文を返します |
| `scope.split_by_scope` | 役の出力に範囲外の印（`in_scope` と `scope_reason`）の項目を足し、それを読んで範囲外の記録を作ります。方針と、シャードに分ける群が共有します。範囲外かの判定は `findings.judge_scope` が持ちます |
| `disclosed.merge_groups` | 発見役が開示済みの限界として挙げた候補を nit に直し（`disclosed_as_nit`）、検めた組（`vet_groups`）ごとに一件へまとめます |
| `bundle_disclosed.bundle_disclosed` | 開示済みの限界の nit が 2 件以上のときだけ、同じ限界を束ねる役を道具なしで一回起動し、シャードをまたぐ同じ限界を一件にします。起動の失敗と、スキーマに合わない出力のときは、まとめずに指摘をそのまま返します |
| `config_head.config_heads_section` | 設定ファイル（justfile、Makefile、pyproject）の冒頭の宣言を、差分のシャードと反証役の資料に添えます。差分は変更の周りの数行しか渡さないためです |
| `output_models.role_output_model` | LLM の出力を検証して型へ読むモデルを持ちます。claude に渡すスキーマも同じモデルから作ります。未知の項目を拒み、型の変換をしません |
| `mcp.McpSelection` | `--mcps` で選んだ MCP サーバを、`mcp.json` で確かめた名前と設定の不変の値にまとめます。空が「MCP 無し」の唯一の表し方です |
| `language_support.support_of` | 言語アダプタが満たす口と、方針の資料の図（`REGISTRY`）のどれに対応するかを求めます |
| `legacy_nits.legacy_nits_of` | 写す前の古い記録にある方針の捨てた候補を、`runs.json` を読む境で nit の指摘に組み立てます。`show` の一覧だけが通ります |

## 方針の資料

方針の起動には、差分ではなく判断の地図から決定的に作った資料（図と表）を渡します。LLM は使いません。

- ツリー図、シーケンス図、判断の箇所の一覧は `jigane/policy_materials/changed_files.py` が作ります。
- `policy_figures.py`：方針の図の登録簿 `REGISTRY`（16 の図）と、プロンプトの資料の組み立て（`prompt_figure_texts`）を持ちます。図そのものは `jigane/policy_materials/` などの関数が作ります。`signatures` は `PROMPT_OMITTED` に入れ、資料からは省きます（画面と CLI の図は省きません）。
- `policy_tree.py`：方針の資料の三つの材料が共有する、読み取り専用の木を持ちます。作るときに全ファイルを一度だけ読み、言語とテストの判定を一か所で持ちます。
- `policy_repeats.py`：同じ形の繰り返しを木から探します。
- `policy_value_sets.py`：登録簿の「値の組の表」を持ちます。定義の欄の場所には、名前に束ねた定義なら名前を、リテラルの写しなら（リテラル）を添えます。変更したファイルが、組の定義、値を含むファイル、文字列で比べている箇所のどれかに当たる組だけを、テストでない定義の数、続けてコードと文書の数の合計が多い順に並べ、最後の列に、差分で足した行が組の定義、値の文字列、比較のどれに触れたかを出します。
- `policy_field_copies.py`：登録簿の「足した型と同じ項目を持つ型」の表を持ちます。変更した `.py` のファイルと変更後の木の `.py` のファイルを Python の `ast` で読み、変更で足した、または項目が変わったトップレベルの型について、別の型と項目の名前を二つ以上共有する行を出します（先頭が `_` の名前は除きます。クラス本体の型注記の項目と、列挙の値が項目です）。名前と数だけを渡し、コードの文は渡しません。言語アダプタの口に依らない図なので、Python 以外の言語は節ごと省き、対応外の一文も出しません。
- `policy_callers.py`、`policy_callers_ast.py`：登録簿の「足した公開の口と呼ぶ側」を持ちます。変更で足した、または引数を足した公開の名前（先頭が `_` でない関数、メソッド、クラス、列挙の値、引数）を一行ずつ、名前、種類、定義の場所、呼ぶ側の数、呼ぶ側で出します。呼ぶ側は変更の後の木の中を名前で照合して数え、再帰は除きます。言語のアダプタの口には依らず、変更のうち `.py` のファイルだけを Python の `ast` で読みます。Python の行が無ければ節ごと省き、他の言語への対応外の一文は出しません。
- `policy_regex.py`：変更した正規表現の選り分けと、登録簿の「正規表現の表」を持ちます。実装とセキュリティの断片には、同じ選り分けの札を `$shard_checks` で渡します。
- `policy_tables.py`：変更したファイルから読んだ項目の表を、変更前後の差（追加、変更、削除の印）つきで組む共通の骨組み（`diff_table`）を持ちます。
- `policy_commands.py`：登録簿の「副コマンドの引数」の表を持ちます。argparse の `add_argument` を構文木だけから読みます。
- `policy_file_paths.py`：登録簿の「ファイルの名前の組み立て」の表を持ちます。書き出す（または読む）ファイルの名前の組み立てを、関数ごとに構文木だけから読みます。
- `policy_stores.py`：登録簿の「保存物の書く側と読む側」の表を持ちます。JSON、CSV、TSV のファイルの名前の定数、または `to_json` と `from_json` を持つ型で保存物を束ね、書く側と読む側が使う鍵の集合を並べます。鍵の抽出は言語の層（`StoreAccessReader`）が行います。
- `policy_concurrency.py`：登録簿の「並行処理の図」を持ちます。変更した関数ごとに、隔離の領域を列にしたシーケンス図を作り、領域をまたぐ `await` だけを矢印にします。言語アダプタの `ConcurrencyAdapter` から手順を受け取ります。
- `policy_cochange.py`：変更したファイルと git の履歴で一緒に変わったファイルの対を、import の依存の有無（あり、なし、コード以外）と並べた登録簿の「共変更の表」を持ちます。履歴が読めないレビューでは節を省きます。

## プロンプト

`prompts/` に置きます。`common.md` は全役に共通する信頼の境界と事実の扱いで、`adversarial.render_prompt` が差し込みます。

- 発見役：`macro_diagram.md`（方針。図だけの形）、`macro_design.md`（方針。設計書の形。組みは `macro_design.py`、資料は `jigane/policy_materials/design_doc.py`）、`micro.md`、`security.md`
- 方針の二つの形が共有する部品：`macro_standards.md`（欠陥の目録と契約の基準）、`macro_history_rule.md`（前の回の指摘の扱い）。雛形の `$standards` と `$history_rule` に差し込み（`macro_shared.prompt_parts`。`macro_standards.md` の `$discard_reason_table` は findings/discard.py の `DISCARD_REASONS` から生成します）、版の算出にも含めます
- 観点の一覧：`micro.md`、`security.md`、`macro_diagram.md` の `$viewpoints` に、観点の表（`groups/viewpoints.csv`）の問いの文から作った行を差し込みます（`shared_rules.viewpoint_list_text`）。各群の `VIEWPOINTS` も同じ表から導き、版の算出にも差し込む文を含めます
- 方針の一層目：`macro_principles.md`（設計の原則を一つだけ問う雛形。原則ごとに割り当てた図の節だけを資料に入れ、その原則を問える形が図にあるときだけ起動します。組み立ては `policy_principles.principle_prompts`（最大四本）と `principle_prompt`（一本）。呼び出し元は `macro_principles_run.py`）
- 雛形に差し込む共通の規則：`scope_rule.md`（範囲で直せない指摘も出し、`in_scope` を false にする規則。`micro.md`、`security.md`、`macro_standards.md` に差し込みます）、`verifier_scope_rule.md`（範囲外であることを、指摘を崩す理由にしない規則。`rebuttal.md`、`judge_rebuttal.md` に差し込みます）。差し込みは `shared_rules.py` の `SharedRule` で定めます
- 開示済みの限界の束ね：`bundle_disclosed.md`（`bundle_disclosed.py` が起動する役）
- 検証役：`rebuttal.md`（反証）、`judge_rebuttal.md`、`alternative.md`（代案）、`defense.md`（擁護）、`judge_alternative.md`
- 選別：`themes.md`

候補が事実の主張なら反証の形、設計や技術の選択の妥当性を問うなら代案の形で検証します（`adversarial.uses_alternative_form`）。

代案の形は、今は本番の経路から起動しません。代案の形に振るのは観点が M0、M2、M3、M5（方針の観点）の候補だけですが、方針の群は検証を持たず発見役の一回で終えます。検証を呼ぶのは実装とセキュリティの群だけで（`sharded.run_sharded`）、その観点は D、T、S に限られます。代案役、擁護役、代案の判定役（`alternative.md`、`defense.md`、`judge_alternative.md`）は、方針の群から検証を外したときに経路が残ったもので、意図して止めたものではありません（開発の決定 alternative-roles-unreachable-recorded）。消すか戻すかは公開の後に決めます。試験（`tests/test_adversarial.py`）は `adversarial.verify` を直に呼んで、この形を確かめています。

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`design`、`figures`、`findings`、`git`、`groups`、`judgment_map`、`languages`、`llm_runs`、`markdown`、`policy_materials`、`preconditions`、`pull_request_context`、`regex`、`reviews`、`sequence`、`storage`、`technology_list`

読まれるサブパッケージ：`commands`

<!-- END GENERATED: dependencies -->

- `llm_runs.claude` で CLI を起動し、`reviews.lifecycle` から対象を、`findings.records` から指摘の番号と統合を、`languages` から資料のための構文解析を読みます。`design.review_documents` から付属の文書の読み込みを、`design.division` から設計書の形の差し込みの文を読みます。
- `commands.subcommands` の `run`、`estimate`、`show`、`usage` が群を呼び、`commands.failures` は群の例外を失敗に変えます。

## Q&A

### なぜ方針の状態は実装とセキュリティを止めないのですか

方針の状態は実装とセキュリティを止めず、実行の前に一行で案内します（`order.policy_notice`、`tests/test_policy_gate.py`）。方針の必読を直すまで実装のレビューを始められない関門は、人の判断より道具の判断を優先させていたためです。

### なぜ方針の資料は LLM も差分も使わず作り、資料の文面を直接検査するのですか

方針の資料は LLM も差分も使わず、資料の文面を直接検査します（`tests/test_policy_materials.py`、`tests/test_policy_repeats.py`）。差分やコードを読ませると、方針ではなくコードの細かな書き方に引きずられた実装の指摘を出しやすくなるためです。

### なぜ検証役のプロンプトには版を付けるのですか

検証役のプロンプトには版を付け、実行の記録に残します（`adversarial.stamp_prompt`、`tests/test_usage.py`）。プロンプトを直すたびに使用量と成果の変化を同じ物差しで比べるには、同じ版の結果どうしと違う版の結果どうしを分けられる必要があるためです。
