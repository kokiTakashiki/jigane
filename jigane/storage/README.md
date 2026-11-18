# jigane.storage

データの置き場の読み書き、保存物の形の検査、読み書きの失敗を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `store` | データホスト（根は新しく作るときだけ 0700 にします。既にある根の権限は変えず、所有者が違うか group か other に書き込み権限があれば止めます）とレビューの識別子を持ちます |
| `locking` | OS のファイルロックと、レビューのディレクトリの状態を排他にする `review_lock` を持ちます。錠のファイルと保存物の読みは `open_no_follow` で symlink を辿らずに開き（symlink なら `StoredFileSymlinkError`）、`O_NOFOLLOW` の無い Windows は `os.lstat` で前後を検査しますが、検査と `os.open` の間の差し替えは Windows では塞げません |
| `atomic_write` | 推測できない名前の一時のファイル（権限 0600）へ書いてから置き換える原子的な書き込みを持ちます。保存先が symlink なら `StoredFileSymlinkError` で止めます |
| `symlink_guard` | symlink（Windows ではリパースポイントも）かを辿らずに判定する `is_link_like` と、その例外 `StoredFileSymlinkError` を持ちます。`locking`、`atomic_write`、`real_directory` が同じ判定と例外を使います |
| `review_creation` | レビューの作成の受け付け、仮の置き場の据え付け、保存の宛先を持ちます |
| `review_access` | 既にあるレビューの保存したファイルの読み（symlink は辿らず `StoredFileSymlinkError`。通常のファイルだけを読み、FIFO、デバイス、ディレクトリは `StoredFileNotRegularError` で拒む。判定は開いた後の `os.fstat` で行う。開く前の `lstat` は判定と `open` の間に差し替えられ、書き手のいない FIFO は `open` や `read` が戻らなくなるため、`O_NONBLOCK` で開いて fd を調べる。ディレクトリと名前で読む `read_stored_file` と、通常のファイルが無ければ `None` を返す `read_stored_file_if_present` を含む）と存在の確認（symlink は無いものとして `False`）と、状態を排他で書く `locked_review` を持ちます |
| `store_format` | 保存物の版の判別と、古い版から現行の版への上げ方を持ちます |
| `store_suffixes` | 保存物のファイルの拡張子の一覧を持ちます。設計書の読み取りとコードの読み取りが同じ一覧を引きます |
| `real_directory` | 置き場のディレクトリが通常のディレクトリであることを確かめる `require_real_directory` を持ちます。symlink は `StoredFileSymlinkError`、ファイルなどは `NotARealDirectoryError` で止めます |
| `review_error` | 保存物の読み書きの失敗を表す例外を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`findings`、`flaky`、`judgment_map`、`languages`、`publishing`、`pull_request_context`、`repository_docs`、`reviews`、`skills`、`static_analysis`

<!-- END GENERATED: dependencies -->
