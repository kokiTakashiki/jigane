# 配布物を検証する

[README に戻る](../README.md)

PyPI の配布物が、公開リポジトリから Trusted Publishing で公開されたことを確かめられます。`pypi-attestations` は `pip install pypi-attestations` か `uv tool install pypi-attestations` で入れます。

```bash
pip download jigane==<version> --no-deps -d dist
pypi-attestations verify pypi --repository https://github.com/kokiTakashiki/jigane dist/<wheel のファイル名>
```

配布物が公開リポジトリの `release.yml` で作られたことは、GitHub の artifact attestation で確かめられます。GitHub CLI（`gh`）を使います。`--signer-workflow` で、署名したワークフローを `release.yml` に絞ります。

```bash
gh attestation verify dist/<wheel のファイル名> --repo kokiTakashiki/jigane \
  --signer-workflow kokiTakashiki/jigane/.github/workflows/release.yml
```

リリースには、利用規約（`TERMS.ja.md`、`TERMS.en.md`）、プライバシーポリシー（`PRIVACY.ja.md`、`PRIVACY.en.md`）、依存の一覧の SBOM（`sbom.cdx.json`）を添付します。規約とプライバシーポリシーは、`verify` が確かめたタグの木のものです。SBOM にも artifact attestation を付けるので、同じ `gh attestation verify` で検証できます。
