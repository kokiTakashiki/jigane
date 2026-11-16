# 対応する Python のバージョン

この文書は、jigane が対応する Python のバージョンと、そのバージョンを決めている設定の役割を定めます。Python のパッケージの仕様は、バージョンの指定の扱いをインストーラーやツールにある程度任せています。そのため jigane では、どの設定が何を約束するのかを、ここで決めておきます。

## 対応するバージョン

jigane 0.1.9 が対応する Python は、3.12 から 3.14 までです。

下限の 3.12 は、`pyproject.toml` の `requires-python = ">=3.12"` で宣言しています。jigane は、この下限より古い Python で動くことを約束しません。

上限は、リリースの前の検査で確かめた一番新しいバージョンです。上限より新しい Python でも入れられることはありますが、動くことは確かめていません。

リリースの前の検査では、下限の 3.12、上限の 3.14、開発に使う 3.13 の三つのバージョンで、同じテストを走らせます。

## 設定の役割

`pyproject.toml` の `requires-python` は、jigane が動くと約束する Python のバージョンの下限を表します。この値は、配布物（wheel と sdist）の `Requires-Python` に写ります。インストーラーがこの値を見て入れるバージョンを選ぶかどうかは、インストーラーに任せます。

リポジトリの `.python-version` は、開発とゲートで使う Python のバージョンを一つに揃えるためだけに置いています。uv は、このファイルを既定の Python のバージョンとして使います。このファイルは配布物には入らないので、PyPI から jigane を入れる人には効きません。

## バージョンを変えるとき

下限か上限を変えるときは、次の三つを同じ変更で直します。

1. `pyproject.toml` の `requires-python`
2. リリースの前の検査で走らせるバージョンの一覧
3. この文書の「対応するバージョン」の節

開発に使うバージョン（`.python-version`）は、下限と上限のあいだのバージョンから選びます。

## 出典

- Python Packaging User Guide, "pyproject.toml specification" の `requires-python`。`https://packaging.python.org/en/latest/specifications/pyproject-toml/`
- Python Packaging User Guide, "Core metadata specifications" の `Requires-Python`。インストーラーがこの値を見て入れるバージョンを選んでよいことが書かれています。`https://packaging.python.org/en/latest/specifications/core-metadata/`
- uv の文書, "Python versions" の `.python-version` の節。`https://docs.astral.sh/uv/concepts/python-versions/`
