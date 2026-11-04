# 言語を追加する

[README に戻る](../README.md)

プラグインのパッケージから言語を追加できます。パッケージを jigane と同じ環境に入れ（`uv tool install` で入れた jigane には `uv tool install jigane --with <パッケージ>`、`pipx` で入れた jigane には `pipx inject jigane <パッケージ>`、それ以外は `pip install` か `uv pip install`）、`jigane languages enable <名前>` で有効にします。

配布が複数あって言語名が重なるときは `--distribution <配布名>` で選びます。

`enable` は、プラグインのコードを読み込む前に、信頼の注意と配布の名前と版を示します。端末で y と答えたときだけ、コードを読み込んで検証し、データの置き場の `languages.json` に記録します。記録するのは、言語と配布の名前と版、承認した時点の配布の中身（RECORD に載る各ファイルの sha256）です。RECORD の無い配布は有効にできません。

起動のたびに、読み込む前に配布の中身を記録と照らします。版が違うときも、同じ版のままファイルが変わったり欠けたりしたときも、読み込みません。配布を更新したら、もう一度 `jigane languages enable <名前>` で確認を取ります。中身を記録する前の形の記録は読めますが、承認としては使わないので、もう一度 `enable` を求めます。

中身が変わったときは、変わった、欠けた、増えたファイルの相対パスを種類つきで、合わせて最大 5 件まで理由に示します（ハッシュの値は示しません）。`enable` の確認でも同じ理由を示します。

`jigane languages disable <名前>` で外します。

文法は、導入の後に `jigane languages fetch` で取得します。照合は「[文法の取得](data-destinations.md#文法の取得)」の節にあります。

`jigane languages list [--json]` は、言語ごとの出どころ、状態、拡張子、使える図と対応外の図を示します。有効にした言語が使えないと、`languages` の副コマンド以外は止まります。

作り方の例は `examples/jigane-language-kotlin/README.md` にあります。

追加の言語のコードと文法の共有ライブラリは、jigane と同じ権限で動きます。jigane は審査しません。出どころを確かめてから有効にします。詳しくは SECURITY.md の「追加の言語」を読んでください。
