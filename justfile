# jigane の作業の入口。macOS・Linux の make と同じ作業を持ち、Windows ではこちらを使う。
# 生成物なので直接直さない。entrypoints.toml を直し、`just generate` で生成する。
# uv が要る。無ければ、公式の方法で導入してから再実行してください: https://docs.astral.sh/uv/getting-started/installation/

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# 本文の行は既定で画面に出さない。make は本文の行頭の @ で同じ表示にしている。
# set quiet の下では行頭の @ を付けても表示には切り替わらない（just 1.58 で確認）。
# entrypoints.toml で show_commands を真にした作業は、走らせる前に各コマンドの行を出すため、
# レシピに [no-quiet] を付けている。
set quiet

# 可変長引数 `*args` は既定では 1 本の空白区切り文字列に結合されてから展開されるため、
# `--intent "a b"` のように空白を含む値を渡すと `a` と `b` に分かれてしまう。`quote()` は
# 要素ごとに単一引用符で包んで渡し直すが、要素単位で効かせるには可変長引数を「文字列の
# リスト」として保持する必要があり、これは `set lists`（`set unstable` の配下）で有効になる:
# https://github.com/casey/just/blob/master/GRAMMAR.md
# `quote()` は just 自身が展開する文字列操作なので、sh・PowerShell のどちらが本文を実行しても
# 単一引用符で囲われた 1 引数として渡る。
#
# FIXME(just-quote-powershell): `quote()` は単一引用符を sh の規則（`'\''`）でエスケープする。
# PowerShell の規則（`''`）とは異なるため、値に単一引用符を含む引数は Windows で壊れうる。
# 直すときは、値に単一引用符を含む引数（例: `--reason "a's b"`）を Windows で渡し、
# 元の文字列がそのまま届くことを確かめる。
set unstable
set lists

# 外で有効にした仮想環境を uv へ渡さない。渡すと、uv が自分のプロジェクトの環境と食い違う
# 警告を呼び出しのたびに出す。uv の --active は外の環境へ入れてしまうので使わない。
# PYTHONPATH も渡さない。空の要素があるとカレントディレクトリが import の道に入り、
# --project とは別の jigane を読むため。
unexport VIRTUAL_ENV
unexport PYTHONPATH

# 引数を渡さず `just` とだけ打ったときは、レシピの一覧を出す。
default:
    just --list

# jigane の副コマンドを、呼び出した場所で走らせる。
jigane *args:
    uv run --locked --directory {{quote(invocation_directory())}} --project {{quote(justfile_directory())}} jigane {{quote(args)}}

# 依存を uv.lock のとおりに入れ、文法を取得する（組み込みと、例のパッケージの言語の分）。
[no-quiet]
setup:
    uv sync --locked
    uv run --locked python -m jigane.languages.fetch_main fetch
    uv run --locked python scripts/fetch_example_grammars.py
    uv run --locked python scripts/gate_stamp.py install-hook

# 作業ツリーが setup 済みかを、通信せずに確かめる（.venv と文法の置き場のファイルの有無だけ。setup は走らせない。無ければ一行の案内を出して止まる）。
preflight:
    uv run --no-project python {{quote(justfile_directory() / "scripts/check_setup.py")}} --env {{quote(justfile_directory())}}

# 規則・整形・型（Linux・macOS と win32 の両方として読む）・Markdown・依存宣言・使われていないコード・層の向き・外への送り先・依存のライセンス・呼び出しの形（ast-grep）を検査する（直さない）。
[no-quiet]
lint:
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked mypy
    uv run --locked mypy --platform win32 --cache-dir .mypy_cache/win32
    uv run --locked rumdl check .
    uv run --locked deptry jigane
    uv run --locked vulture
    uv run --locked python scripts/generate_import_contracts.py --check
    uv run --locked python scripts/generate_package_readme.py --check
    uv run --locked python scripts/generate_readme_dependencies.py --check
    uv run --locked lint-imports
    uv run --locked python scripts/check_workflows.py
    uv run --locked python scripts/check_egress.py
    uv run --locked python scripts/check_dependency_licenses.py
    uv run --locked python scripts/check_noqa_reasons.py
    uv run --locked ast-grep test
    uv run --locked ast-grep scan jigane tests scripts docs

# 整形し、自動で直せる違反を直す。
[no-quiet]
fmt:
    uv run --locked ruff format .
    uv run --locked ruff check --fix .
    uv run --locked rumdl fmt .

# テストを走らせる（JIGANE_HOME は一時の場所へ向ける。macOS・Linux では POSIX だけの試験 tests_posix も走らせる）。
[no-quiet]
test:
    uv run --locked --project {{quote(justfile_directory())}} python {{quote(justfile_directory() / "scripts/with_temp_jigane_home.py")}} uv run --locked pytest

# この定義から Makefile と justfile を、build_parser から README の副コマンドの一覧表を、ClaudeInput から SECURITY.md の Claude Code への入力の表を、層と兄弟の依存の表から import-linter の兄弟の契約を、層の契約と各サブパッケージの README から jigane/README.md の表と層の図を、grimp で測った import から各サブパッケージの README の依存の一覧を作り直す。
generate:
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_entrypoints.py
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_command_table.py
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_claude_inputs_table.py
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_import_contracts.py
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_package_readme.py
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/generate_readme_dependencies.py

# 試験の揺れを測る。乱順の種と並列の数を変えて同じ試験を繰り返し、落ちた試験と種を表にする（引数はそのまま scripts/flaky_hunt.py へ通す。--help を見る。落ちた回の出力は一時の置き場に残す。ゲートには入れない）。
flaky-hunt *args:
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/flaky_hunt.py {{quote(args)}}

# 指定した範囲のコミットの作者と委託者のメールアドレスが GitHub の noreply だけであることを確かめる（引数は git log の版の範囲で必須。例 ARGS="origin/main..HEAD"。呼び出した場所の git リポジトリを見る。ゲートには入れない）。
check-commit-emails *args:
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/check_commit_emails.py --root {{quote(invocation_directory())}} {{quote(args)}}

# 機械のゲート。lint と test を通し、緑で終わったときだけ HEAD に印を書く（push 前の hook が印と照らす）。
check: preflight lint test
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/gate_stamp.py write

# 文書のゲート。Markdown を rumdl で検査し、緑で終わったときだけ HEAD に docs の印を書く（文書だけを変えた push は、この印で通る）。
docs-check:
    uv run --locked rumdl check .
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/gate_stamp.py write --gate docs

# 変更に関わる試験だけを走らせる（main との merge-base からの差分と未コミットの変更が対象。--list は選ぶだけ。全体は CI で確かめる）。
[no-quiet]
changed-tests *args:
    uv run --locked --project {{quote(justfile_directory())}} python {{quote(justfile_directory() / "scripts/with_temp_jigane_home.py")}} uv run --locked --directory {{quote(justfile_directory())}} python scripts/changed_tests.py {{quote(args)}}

# 軽い機械のゲート。lint と、変更に関わる試験だけを通す（全体の試験は push して CI で確かめる）。
quick-check: preflight lint changed-tests

# uv.lock が固定した依存の既知の脆弱性を pip-audit で確かめる（見つかれば失敗する。ネットワークを使い、同じ uv.lock でも日によって結果が変わるため、ゲートには入れない。週ごとの CI と配布の前の確かめが走らせる）。
audit-dependencies:
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/audit_dependencies.py

# GitHub のリポジトリの設定を .github/repository-settings.json の期待と照らして差を出す（引数は --repo owner/name と --profile working|public で必須。例 ARGS="--repo owner/name --profile public"。gh の認証が要る。ゲートには入れない）。
check-repo-settings *args:
    uv run --locked --directory {{quote(justfile_directory())}} python scripts/check_repo_settings.py {{quote(args)}}
