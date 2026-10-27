# jigane の作業の入口（macOS・Linux 向け）。Windows は justfile を使う。
# 生成物なので直接直さない。entrypoints.toml を直し、`make generate` で生成する。

# 引数は `make <target> ARGS="..."` の形で渡す。`$(ARGS)` は文字どおりに展開されてから sh に
# 渡るため、空白を含む値は ARGS の中で引用したときに限って 1 引数のまま届く
# （例: `make jigane ARGS="new files src --name x --intent 'a b'"`）。
ARGS =

# 外で有効にした仮想環境を uv へ渡さない。渡すと、uv が自分のプロジェクトの環境と食い違う
# 警告を呼び出しのたびに出す。uv の --active は外の環境へ入れてしまうので使わない。
# PYTHONPATH も渡さない。空の要素があるとカレントディレクトリが import の道に入り、
# --project とは別の jigane を読むため。
unexport VIRTUAL_ENV
unexport PYTHONPATH

# この Makefile の置き場。別の木から `make -f <jigane>/Makefile jigane` で呼んでも、
# jigane 自身の木をプロジェクトにするために使う。
# 空白を含むパスでも語に分かれないよう、dir や realpath（空白で語に分ける）ではなくシェルで求める。
# ここは include より前なので、MAKEFILE_LIST の中身はこの Makefile だけである。
PROJECT_DIR := $(shell cd "$$(dirname "$(strip $(MAKEFILE_LIST))")" && pwd -P)/

# 依存の順に 1 つずつ走らせる（check は lint のあとに test）。
.NOTPARALLEL:

# uv が無ければ、導入の案内を出して止まる。uv の導入はしない。
UV_INSTALL_MESSAGE = 公式の方法で導入してから再実行してください
UV_INSTALL_URL = https://docs.astral.sh/uv/getting-started/installation/
define require_uv
@command -v uv >/dev/null 2>&1 || { \
	echo "  x uv が見つかりません。$(UV_INSTALL_MESSAGE): $(UV_INSTALL_URL)" >&2; exit 1; }
endef

.PHONY: audit-dependencies changed-tests check check-commit-emails check-repo-settings docs-check flaky-hunt fmt generate help jigane lint preflight quick-check setup test

# 引数を渡さず `make` とだけ打ったときは、使い方と作業の一覧を出す。
.DEFAULT_GOAL := help
help:
	@echo "使い方: make <target> [ARGS=\"...\"]"
	@echo ""
	@echo "  jigane               jigane の副コマンドを、呼び出した場所で走らせる"
	@echo "  setup                依存を uv.lock のとおりに入れ、文法を取得する（組み込みと、例のパッケージの言語の分）"
	@echo "  preflight            作業ツリーが setup 済みかを、通信せずに確かめる（.venv と文法の置き場のファイルの有無だけ。setup は走らせない。無ければ一行の案内を出して止まる）"
	@echo "  lint                 規則・整形・型（Linux・macOS と win32 の両方として読む）・Markdown・依存宣言・使われていないコード・層の向き・外への送り先・依存のライセンス・呼び出しの形（ast-grep）を検査する（直さない）"
	@echo "  fmt                  整形し、自動で直せる違反を直す"
	@echo "  test                 テストを走らせる（JIGANE_HOME は一時の場所へ向ける。macOS・Linux では POSIX だけの試験 tests_posix も走らせる）"
	@echo "  generate             この定義から Makefile と justfile を、build_parser から README の副コマンドの一覧表を、ClaudeInput から SECURITY.md の Claude Code への入力の表を、層と兄弟の依存の表から import-linter の兄弟の契約を、層の契約と各サブパッケージの README から jigane/README.md の表と層の図を、grimp で測った import から各サブパッケージの README の依存の一覧を作り直す"
	@echo "  flaky-hunt           試験の揺れを測る。乱順の種と並列の数を変えて同じ試験を繰り返し、落ちた試験と種を表にする（引数はそのまま scripts/flaky_hunt.py へ通す。--help を見る。落ちた回の出力は一時の置き場に残す。ゲートには入れない）"
	@echo "  check-commit-emails  指定した範囲のコミットの作者と委託者のメールアドレスが GitHub の noreply だけであることを確かめる（引数は git log の版の範囲で必須。例 ARGS="origin/main..HEAD"。呼び出した場所の git リポジトリを見る。ゲートには入れない）"
	@echo "  check                機械のゲート。lint と test を通し、緑で終わったときだけ HEAD に印を書く（push 前の hook が印と照らす）"
	@echo "  docs-check           文書のゲート。Markdown を rumdl で検査し、緑で終わったときだけ HEAD に docs の印を書く（文書だけを変えた push は、この印で通る）"
	@echo "  changed-tests        変更に関わる試験だけを走らせる（main との merge-base からの差分と未コミットの変更が対象。--list は選ぶだけ。全体は CI で確かめる）"
	@echo "  quick-check          軽い機械のゲート。lint と、変更に関わる試験だけを通す（全体の試験は push して CI で確かめる）"
	@echo "  audit-dependencies   uv.lock が固定した依存の既知の脆弱性を pip-audit で確かめる（見つかれば失敗する。ネットワークを使い、同じ uv.lock でも日によって結果が変わるため、ゲートには入れない。週ごとの CI と配布の前の確かめが走らせる）"
	@echo "  check-repo-settings  GitHub のリポジトリの設定を .github/repository-settings.json の期待と照らして差を出す（引数は --repo owner/name と --profile working|public で必須。例 ARGS="--repo owner/name --profile public"。gh の認証が要る。ゲートには入れない）"

# jigane の副コマンドを、呼び出した場所で走らせる。
jigane:
	$(call require_uv)
	@uv run --locked --directory "$(CURDIR)" --project "$(PROJECT_DIR)" jigane $(ARGS)

# 依存を uv.lock のとおりに入れ、文法を取得する（組み込みと、例のパッケージの言語の分）。
setup:
	$(call require_uv)
	uv sync --locked
	uv run --locked python -m jigane.languages.fetch_main fetch
	uv run --locked python scripts/fetch_example_grammars.py
	uv run --locked python scripts/gate_stamp.py install-hook

# 作業ツリーが setup 済みかを、通信せずに確かめる（.venv と文法の置き場のファイルの有無だけ。setup は走らせない。無ければ一行の案内を出して止まる）。
preflight:
	$(call require_uv)
	@uv run --no-project python "$(PROJECT_DIR)"/scripts/check_setup.py --env "$(PROJECT_DIR)"

# 規則・整形・型（Linux・macOS と win32 の両方として読む）・Markdown・依存宣言・使われていないコード・層の向き・外への送り先・依存のライセンス・呼び出しの形（ast-grep）を検査する（直さない）。
lint:
	$(call require_uv)
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
fmt:
	$(call require_uv)
	uv run --locked ruff format .
	uv run --locked ruff check --fix .
	uv run --locked rumdl fmt .

# テストを走らせる（JIGANE_HOME は一時の場所へ向ける。macOS・Linux では POSIX だけの試験 tests_posix も走らせる）。
test:
	$(call require_uv)
	uv run --locked --project "$(PROJECT_DIR)" python "$(PROJECT_DIR)"/scripts/with_temp_jigane_home.py uv run --locked pytest
	uv run --locked --project "$(PROJECT_DIR)" python "$(PROJECT_DIR)"/scripts/with_temp_jigane_home.py uv run --locked pytest tests_posix

# この定義から Makefile と justfile を、build_parser から README の副コマンドの一覧表を、ClaudeInput から SECURITY.md の Claude Code への入力の表を、層と兄弟の依存の表から import-linter の兄弟の契約を、層の契約と各サブパッケージの README から jigane/README.md の表と層の図を、grimp で測った import から各サブパッケージの README の依存の一覧を作り直す。
generate:
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_entrypoints.py
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_command_table.py
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_claude_inputs_table.py
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_import_contracts.py
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_package_readme.py
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/generate_readme_dependencies.py

# 試験の揺れを測る。乱順の種と並列の数を変えて同じ試験を繰り返し、落ちた試験と種を表にする（引数はそのまま scripts/flaky_hunt.py へ通す。--help を見る。落ちた回の出力は一時の置き場に残す。ゲートには入れない）。
flaky-hunt:
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/flaky_hunt.py $(ARGS)

# 指定した範囲のコミットの作者と委託者のメールアドレスが GitHub の noreply だけであることを確かめる（引数は git log の版の範囲で必須。例 ARGS="origin/main..HEAD"。呼び出した場所の git リポジトリを見る。ゲートには入れない）。
check-commit-emails:
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/check_commit_emails.py --root "$(CURDIR)" $(ARGS)

# 機械のゲート。lint と test を通し、緑で終わったときだけ HEAD に印を書く（push 前の hook が印と照らす）。
check: preflight lint test
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/gate_stamp.py write

# 文書のゲート。Markdown を rumdl で検査し、緑で終わったときだけ HEAD に docs の印を書く（文書だけを変えた push は、この印で通る）。
docs-check:
	$(call require_uv)
	@uv run --locked rumdl check .
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/gate_stamp.py write --gate docs

# 変更に関わる試験だけを走らせる（main との merge-base からの差分と未コミットの変更が対象。--list は選ぶだけ。全体は CI で確かめる）。
changed-tests:
	$(call require_uv)
	uv run --locked --project "$(PROJECT_DIR)" python "$(PROJECT_DIR)"/scripts/with_temp_jigane_home.py uv run --locked --directory "$(PROJECT_DIR)" python scripts/changed_tests.py $(ARGS)

# 軽い機械のゲート。lint と、変更に関わる試験だけを通す（全体の試験は push して CI で確かめる）。
quick-check: preflight lint changed-tests

# uv.lock が固定した依存の既知の脆弱性を pip-audit で確かめる（見つかれば失敗する。ネットワークを使い、同じ uv.lock でも日によって結果が変わるため、ゲートには入れない。週ごとの CI と配布の前の確かめが走らせる）。
audit-dependencies:
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/audit_dependencies.py

# GitHub のリポジトリの設定を .github/repository-settings.json の期待と照らして差を出す（引数は --repo owner/name と --profile working|public で必須。例 ARGS="--repo owner/name --profile public"。gh の認証が要る。ゲートには入れない）。
check-repo-settings:
	$(call require_uv)
	@uv run --locked --directory "$(PROJECT_DIR)" python scripts/check_repo_settings.py $(ARGS)
