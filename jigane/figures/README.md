# jigane.figures

方針の図（表、Mermaid、ツリー、シーケンス図）の構造の型を持ちます。図の項目の並びはここの frozen dataclass が唯一の形で、プロンプトの文と JSON の辞書はこの型から作ります。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `models` | 図の型（`TableFigure`、`MermaidFigure`、`TreeFigure`、`SequenceFigure` など）と、文と JSON への変換を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`markdown`

読まれるサブパッケージ：`commands`、`policy_materials`、`sequence`、`skills`

<!-- END GENERATED: dependencies -->

- 表のセルの整形を `jigane.markdown.cells` に頼ります。
