# 古い形の保存物の素材

本物の置き場から形ごとに選び、秘密を消した素材を縮めたもの。`tests/test_store_compat.py` が、今の読み口で読めることを縛る。

## 配置

- `<種類>/sNN/owner/repo/<review>/` はレビューの置き場の形のままで、review.json、map.json、background.json、approval.json、gates.json を添えてある。approval.json は承認の制約を外す前の保存物が持っていたもので、今の読み口は読まない。素材に残すのは、それが置き場に在っても他のファイルが読めることを縛るため。
- `root/` は置き場の根の素材（`root/mcp/mcp.json`）。レビューの置き場ではないので、試験は走査しない。
- 形の一覧と元の件数は CATALOG.md。素材は CATALOG.md の識別子（approval/s01 など）で指す。ただし CATALOG.md は縮める前の素材の目録で、タグの欄は縮めた後の素材と一致しない。
- pr_state.json は、PR の状態を CLI が取って保存していたころの保存物で、今の読み口は読まず、新しいレビューでは書かない。素材に残すのは、それが置き場に在っても他のファイルが読めることを縛るため。
- background.json の issues と review.json の pull_request.linked_issues は、背景から issue を外す前の保存物が持っていたもので、今の読み口は版の移しで落とす。素材に残すのは、古い保存物が読めることを縛るため。

## 素材の作り直し

素材の根（形ごとに選んだ元の写し）から、次で縮めて書き出す。

```text
uv run python scripts/shrink_store_fixtures.py <素材の根> tests/fixtures/store
```

- 出力先は tests/fixtures/store の下に限る。引数が足りないか素材の根が無いときは、何も消さずに失敗する。出力先の README.md と CATALOG.md は消さない。素材の根に今の tests/fixtures/store を指しても作り直せる。
- 配列は、鍵の集合と列挙の値（kind、severity、triage、change_kind など）が同じ行を一つにまとめ、形ごとの先頭の行を残す。要素が葉の配列でも、鍵が列挙の欄か識別子の欄（finding_ids、picked、held、stale_groups）なら、値ごとに残す。
- gates の keys と regions の辞書は、値の形が同じ項目を一つにまとめる。line_counts は鍵が語彙の値なので、まとめずにそのまま残す。
- 素材の作成時に自由文と一緒に "text" へ潰れた列挙の欄（tool_claim.basis、stale_groups）は読める値へ戻す。
- 識別子の区切りを直す。素材は範囲と設計書を `owner/repo:@x`、`owner/repo:+x` と書いていたが、今の識別子は `owner/repo@x`、`owner/repo+x` である。

## 秘密の確認

`tests/test_store_compat.py` の `test_no_private_names` が、素材の全体と README.md と CATALOG.md に次の語が出ないことを縛る。語の一覧は試験の中にある。

- ローカルの絶対パスと置き場の位置
- 利用者の名前とメール
- リポジトリと組織の名前（製品名も、鍵 `jigane_version` を除いて出ないこと）

トークンの形と秘密の語（`password`、`secret`、`api_key`、`token`）は、素材を作ったときに手で grep しただけで、試験は見ない。`token` は input_tokens など数値の欄の鍵に当たるだけである。自由文の欄はすべて "text" に置き換わっている。

## 読めない素材

今の main で読めなかった素材は無い。試験の対象から外した素材は無く、`unreadable/` は置いていない。
