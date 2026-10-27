# jigane.skills

LLM に指摘を作らせるスキル群を持ちます。レビューの群を実行する層です。Claude Code CLI を呼ぶ発見役と、候補を検証する役を組み立て、指摘として正規化して保存します。入力はレビューの対象（`jigane.reviews.lifecycle.Review`）と判断の地図で、出力は `findings` と実行の記録です。CLI の表示は受け持ちません。

## 群

| 内部名 | 表示名 | 入口 | 内容 |
| --- | --- | --- | --- |
| `macro` | 方針 | `macro.run_macro` | 変更の道筋の誤りを、道具を渡さない一回の起動で、図と表だけから外形を問います。起動は見直しと範囲の判断まで行い、最終の指摘を返します。検証は行わず、`adjudication` を持ちません |
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

## 方針の資料

方針の起動には、差分ではなく判断の地図から決定的に作った資料（図と表）を渡します。LLM は使いません。

- ツリー図、シーケンス図、判断の箇所の一覧は `jigane/policy_materials/changed_files.py` が作ります。
- `policy_figures.py`：クラス図、ER 図、判別の流れ図、読む側の対応を作ります（材料は `jigane/policy_materials/` の `classes.py`、`er.py`、`discriminators.py`、`declarations.py`）。
- `policy_tree.py`：方針の資料の三つの材料が共有する、読み取り専用の木を持ちます。作るときに全ファイルを一度だけ読み、言語とテストの判定を一か所で持ちます。
- `policy_repeats.py`：同じ形の繰り返しを木から探します。
- `policy_regex.py`：変更した正規表現の選り分けと、登録簿の「正規表現の表」を持ちます。実装とセキュリティの断片には、同じ選り分けの札を `$shard_checks` で渡します。

## プロンプト

`prompts/` に置きます。`common.md` は全役に共通する信頼の境界と事実の扱いで、`adversarial.render_prompt` が差し込みます。

- 発見役：`macro_diagram.md`（方針。図だけの形）、`macro_design.md`（方針。設計書の形。組みは `macro_design.py`、資料は `jigane/policy_materials/design_doc.py`）、`micro.md`、`security.md`
- 方針の二つの形が共有する部品：`macro_standards.md`（欠陥の目録と契約の基準）、`macro_history_rule.md`（前の回の指摘の扱い）。雛形の `$standards` と `$history_rule` に差し込み（`macro_shared.prompt_parts`。`macro_standards.md` の `$discard_reason_table` は findings/discard.py の `DISCARD_REASONS` から生成します）、版の算出にも含めます
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
