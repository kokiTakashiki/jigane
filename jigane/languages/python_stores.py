"""保存物の鍵の抽出（Python）。構文木（ast）だけから読み、実行も評価もしない。

保存物は、JSON と CSV と TSV のファイルの名前の定数で束ねるか、
`to_json` と `from_json` を持つ型で束ねる。関数の本体は鍵の集合だけを読み、値は読まない。
束ねと突き合わせは言語に依らない側（`jigane.skills.policy_stores`）が行う。
"""

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from jigane.languages.base import StoreAccess, StoreFormat, StoreRole, StoreSide

_SUFFIXES = (".json", ".jsonl", ".csv", ".tsv")
_READ_SOURCES = {"load", "loads", "DictReader"}
_WRITE_CALLS = {"dump", "dumps", "writerow", "writerows", "DictWriter"}
_WRITE_METHODS = {"to_json", "to_dict"}
_READ_METHODS = {"from_json", "from_dict"}

type _Function = ast.FunctionDef | ast.AsyncFunctionDef


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _constants(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and (value := _string(node.value)) is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = value
    return found


def _fields_of(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Attribute):
        return base.attr
    return base.id if isinstance(base, ast.Name) else ""


def _enum_members(cls: ast.ClassDef) -> dict[str, str]:
    """列挙型のメンバーから文字列の値への対応。文字列の値を持たない型は空。"""
    bases = {_base_name(b) for b in cls.bases}
    if not any(b.endswith("Enum") for b in bases):
        return {}
    members: dict[str, str] = {}
    for node in cls.body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            continue
        name = node.targets[0].id
        value = _string(node.value)
        if (
            value is None
            and "StrEnum" in bases
            and isinstance(node.value, ast.Call)
            and _call_name(node.value) == "auto"
        ):
            # StrEnum の auto() は、メンバー名の小文字が値になる
            value = name.lower()
        if value is not None:
            members[name] = value
    return members


type _Key = tuple[str, str | None]


@dataclass
class _Side:
    function: str
    path: str
    kind: StoreFormat
    keys: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)

    def add(self, *keys: _Key | None) -> None:
        for key in keys:
            if key is None:
                continue
            self.keys.add(key[0])
            if key[1] is not None:
                self.labels.add(key[1])


@dataclass
class _Store:
    writers: list[_Side] = field(default_factory=list)
    readers: list[_Side] = field(default_factory=list)


@dataclass
class _Sink:
    """見出しや辞書を引数で受けて書き出す関数。呼ぶ側の引数が鍵の出どころになる。"""

    parameter: str
    index: int
    kind: StoreFormat


@dataclass
class _Imports:
    """一つのモジュールの import。

    names は (取り込み元のファイル, 元の名前)、modules は別名からファイルへ。
    """

    names: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)


_NO_IMPORTS = _Imports()


class _Modules:
    """リポジトリ内のファイルを、import の書き方から引く。"""

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = set(paths)
        # 絶対 import の起点。リポジトリの根と、__init__.py を持つパッケージの根だけを数える
        self.roots: set[tuple[str, ...]] = {()}
        for path in self.paths:
            directory = PurePosixPath(path).parent
            top = directory
            while (top / "__init__.py").as_posix() in self.paths and top.parts:
                top = top.parent
            if top != directory:
                self.roots.add(top.parts)

    def _file(self, parts: list[str]) -> str | None:
        stem = "/".join(parts)
        candidates = [f"{stem}.py", f"{stem}/__init__.py"] if parts else ["__init__.py"]
        return next((c for c in candidates if c in self.paths), None)

    def absolute(self, path: str, dotted: str) -> str | None:
        # 同じ階層のファイルを名前だけで読み込むスクリプト式の import を先に当てる
        here = list(PurePosixPath(path).parent.parts)
        names = dotted.split(".")
        if (sibling := self._file([*here, *names])) is not None:
            return sibling
        # 末尾の一致では束ねない。外部ライブラリと同名のファイルを取り違えるため
        found = {f for root in self.roots if (f := self._file([*root, *names])) is not None}
        # 起点ごとに別のファイルへ当たるなら、どれとも決められない
        return next(iter(found)) if len(found) == 1 else None

    def relative(self, path: str, level: int, module: str | None) -> str | None:
        base = list(PurePosixPath(path).parent.parts)
        if level - 1 > len(base):
            return None
        base = base[: len(base) - (level - 1)]
        return self._file([*base, *(module.split(".") if module else [])])


def _resolve_import(modules: _Modules, path: str, level: int, module: str | None) -> str | None:
    if level:
        return modules.relative(path, level, module)
    return modules.absolute(path, module) if module else None


def _add_from_import(node: ast.ImportFrom, path: str, modules: _Modules, found: _Imports) -> None:
    target = _resolve_import(modules, path, node.level, node.module)
    for alias in node.names:
        if alias.name == "*":
            continue
        local = alias.asname or alias.name
        found.names[local] = (target, alias.name)
        dotted = f"{node.module}.{alias.name}" if node.module else alias.name
        if (submodule := _resolve_import(modules, path, node.level, dotted)) is not None:
            found.modules[local] = submodule


def _add_plain_import(node: ast.Import, path: str, modules: _Modules, found: _Imports) -> None:
    for alias in node.names:
        if alias.asname is None and "." in alias.name:
            # `import a.b` は `a.b.X` の形で参照され、一段の属性では辿れない
            continue
        if (target := modules.absolute(path, alias.name)) is not None:
            found.modules[alias.asname or alias.name] = target


def _imports_of(tree: ast.Module, path: str, modules: _Modules) -> _Imports:
    found = _Imports()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            _add_from_import(node, path, modules, found)
        elif isinstance(node, ast.Import):
            _add_plain_import(node, path, modules, found)
    return found


_REEXPORT_DEPTH = 5


@dataclass
class _Enum:
    name: str
    members: dict[str, str]


@dataclass
class _Vocabulary:
    # 定義は (定義したファイル, 名前) で引く。名前だけの辞書にしない
    constants: dict[str, dict[str, str]] = field(default_factory=dict)
    enums: dict[str, dict[str, _Enum]] = field(default_factory=dict)
    sequences: dict[str, dict[str, ast.expr]] = field(default_factory=dict)
    functions: dict[str, dict[str, list[_Function]]] = field(default_factory=dict)
    # 受け口でない関数も None で載せる。import の先に定義があるとき、名前の一意へ逃げないため
    sinks: dict[str, dict[str, _Sink | None]] = field(default_factory=dict)
    classes: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    imports: dict[str, _Imports] = field(default_factory=dict)

    def _through_imports[V](
        self, table: dict[str, dict[str, V]], path: str, name: str, depth: int = 0
    ) -> tuple[str, V] | None:
        """path のモジュールが name として持つ定義。再公開（import の再 export）も辿る。"""
        defined = table.get(path, {})
        if name in defined:
            return path, defined[name]
        imported = self.imports.get(path, _NO_IMPORTS).names.get(name)
        if imported is None or imported[0] is None or depth >= _REEXPORT_DEPTH:
            return None
        return self._through_imports(table, imported[0], imported[1], depth + 1)

    def find[V](
        self, table: dict[str, dict[str, V]], path: str, name: str
    ) -> tuple[str, V] | None:
        """path から見える name の定義と、それを定義したファイル。

        import をたどり、たどれなければ同じモジュール、次に名前がリポジトリで一意のものを引く。
        ただし import の文がある名前は、一意の引きをしない。
        一意でなければ None で、推測して束ねない。
        """
        imported = self.imports.get(path, _NO_IMPORTS).names.get(name)
        if imported is not None and imported[0] is not None:
            traced = self._through_imports(table, imported[0], imported[1])
            if traced is not None:
                return traced
        if name in table.get(path, {}):
            return path, table[path][name]
        if imported is not None:
            # import の文がある名前は、たどれなくても外部や別のファイルを指す。名前の一意へ逃げない
            return None
        owners = [p for p, defined in table.items() if name in defined]
        return (owners[0], table[owners[0]][name]) if len(owners) == 1 else None

    def find_in_module[V](
        self, table: dict[str, dict[str, V]], path: str, alias: str, name: str
    ) -> tuple[str, V] | None:
        """`alias.name` の参照。alias が import したモジュールでなければ None。"""
        module = self.imports.get(path, _NO_IMPORTS).modules.get(alias)
        return None if module is None else self._through_imports(table, module, name)

    def find_called[V](
        self, table: dict[str, dict[str, V]], path: str, call: ast.Call
    ) -> tuple[str, V] | None:
        """呼び出しの先の定義。`module.f()` は module をたどり、それ以外は名前で引く。"""
        func = call.func
        modules = self.imports.get(path, _NO_IMPORTS).modules
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            # import したモジュールの属性は、そのモジュールの定義だけを見る。
            # 無ければ外の関数で、同名の別の定義へ束ねない
            return self.find_in_module(table, path, func.value.id, func.attr)
        name = _call_name(call)
        return self.find(table, path, name) if name else None

    def _enum(self, node: ast.AST, path: str) -> tuple[str, _Enum] | None:
        if isinstance(node, ast.Name):
            return self.find(self.enums, path, node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self.find_in_module(self.enums, path, node.value.id, node.attr)
        return None

    def _enum_label(self, owner: str, enum: _Enum) -> str:
        # 同名の列挙型が別のファイルにもあるときは、ファイルで区別する
        same = sum(
            enum.name in defined and bool(defined[enum.name].members)
            for defined in self.enums.values()
        )
        return enum.name if same == 1 else f"{enum.name}（{owner}）"

    def _constant_label(self, owner: str) -> str:
        name = Path(owner).name
        same = sum(Path(p).name == name for p, defined in self.constants.items() if defined)
        return f"定数群 {name if same == 1 else owner}"

    def string_constant(self, node: ast.AST, path: str) -> str | None:
        """定数の名前（`NAME` または `module.NAME`）が持つ文字列の値。"""
        found = self._constant(node, path)
        return found[1] if found is not None else None

    def key(self, node: ast.AST, path: str) -> _Key | None:
        """鍵として書かれた式を (文字列の値, 語彙の名前) に解く。語彙のない文字列の名前は None。"""
        text = _string(node)
        if text is not None:
            return text, None
        if isinstance(node, ast.Attribute) and node.attr == "value":
            node = node.value
        if isinstance(node, ast.Attribute):
            enum = self._enum(node.value, path)
            if enum is not None and node.attr in enum[1].members:
                return enum[1].members[node.attr], self._enum_label(*enum)
            node_name = node.attr
        elif isinstance(node, ast.Name):
            node_name = node.id
        else:
            return None
        # 小文字の名前は局所変数と取り違えやすいため、定数は大文字の名前に限る
        if not node_name.isupper():
            return None
        found = self._constant(node, path)
        return (found[1], self._constant_label(found[0])) if found is not None else None

    def _constant(self, node: ast.AST, path: str) -> tuple[str, str] | None:
        if isinstance(node, ast.Name):
            return self.find(self.constants, path, node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self.find_in_module(self.constants, path, node.value.id, node.attr)
        return None


_DEPTH = 6


def _local_values(scope: ast.AST, name: str) -> list[ast.expr]:
    values: list[ast.expr] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                values.append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                values.append(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
        ):
            at = 1 if node.func.attr == "insert" else 0
            if node.func.attr in ("append", "add", "insert", "extend") and len(node.args) > at:
                values.append(node.args[at])
    return values


def _sequence_keys(
    expr: ast.List | ast.Tuple | ast.Set,
    scope: ast.AST | None,
    path: str,
    vocab: _Vocabulary,
    depth: int,
    *,
    indirect: bool,
) -> list[_Key]:
    found: list[_Key] = []
    for element in expr.elts:
        if not indirect:
            if (key := vocab.key(element, path)) is not None:
                found.append(key)
            continue
        item = element.value if isinstance(element, ast.Starred) else element
        found += _expr_keys(item, scope, path, vocab, depth + 1, indirect=True)
    return found


def _name_keys(
    expr: ast.Name, scope: ast.AST | None, path: str, vocab: _Vocabulary, depth: int
) -> list[_Key]:
    found: list[_Key] = []
    values = _local_values(scope, expr.id) if scope is not None else []
    for value in values:
        found += _expr_keys(value, scope, path, vocab, depth + 1, indirect=True)
    if not values and (sequence := vocab.find(vocab.sequences, path, expr.id)) is not None:
        # 別のファイルの並びは、そのファイルの中の名前として読む
        found += _expr_keys(sequence[1], None, sequence[0], vocab, depth + 1, indirect=True)
    return found


def _call_keys(
    expr: ast.Call, scope: ast.AST | None, path: str, vocab: _Vocabulary, depth: int
) -> list[_Key]:
    found: list[_Key] = []
    name = _call_name(expr)
    if name in ("tuple", "list", "sorted", "set", "frozenset") and expr.args:
        found += _expr_keys(expr.args[0], scope, path, vocab, depth + 1, indirect=True)
    if (called := vocab.find_called(vocab.functions, path, expr)) is not None:
        owner_path, functions = called
        for function in functions:
            for node in ast.walk(function):
                if isinstance(node, ast.Return) and node.value is not None:
                    found += _expr_keys(
                        node.value, function, owner_path, vocab, depth + 1, indirect=True
                    )
    return found


def _indirect_keys(
    expr: ast.expr, scope: ast.AST | None, path: str, vocab: _Vocabulary, depth: int
) -> list[_Key]:
    found: list[_Key] = []
    if isinstance(expr, ast.Dict):
        found += [k for key in expr.keys if key is not None and (k := vocab.key(key, path))]
    elif isinstance(expr, ast.Name):
        found += _name_keys(expr, scope, path, vocab, depth)
    elif isinstance(expr, ast.Call):
        found += _call_keys(expr, scope, path, vocab, depth)
    elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        found += _expr_keys(expr.left, scope, path, vocab, depth + 1, indirect=True)
        found += _expr_keys(expr.right, scope, path, vocab, depth + 1, indirect=True)
    elif isinstance(expr, ast.IfExp):
        found += _expr_keys(expr.body, scope, path, vocab, depth + 1, indirect=True)
        found += _expr_keys(expr.orelse, scope, path, vocab, depth + 1, indirect=True)
    return found


def _expr_keys(
    expr: ast.expr,
    scope: ast.AST | None,
    path: str,
    vocab: _Vocabulary,
    depth: int,
    *,
    indirect: bool,
) -> list[_Key]:
    """見出しや辞書の式から鍵を集める。indirect なら名前と関数の呼び出しを辿る。"""
    if (key := vocab.key(expr, path)) is not None:
        return [key]
    if depth > _DEPTH:
        return []
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        return _sequence_keys(expr, scope, path, vocab, depth, indirect=indirect)
    if not indirect:
        # writerows の引数は行のデータで、名前や呼び出しを辿ると値を鍵に数えてしまう
        return []
    return _indirect_keys(expr, scope, path, vocab, depth)


def _store_names(function: ast.AST, path: str, vocab: _Vocabulary) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        text = _string(node)
        if text is None and isinstance(node, (ast.Name, ast.Attribute)):
            text = vocab.string_constant(node, path)
        if text is not None and text.endswith(_SUFFIXES) and "\n" not in text:
            names.add(text)
    return names


def _bind(target: ast.AST, tainted: set[str]) -> None:
    tainted.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))


def _propagate(node: ast.AST, tainted: set[str]) -> None:
    if isinstance(node, ast.Assign) and _derives(node.value, tainted):
        for target in node.targets:
            _bind(target, tainted)
    elif isinstance(node, (ast.For, ast.comprehension)) and _derives(node.iter, tainted):
        _bind(node.target, tainted)
    elif isinstance(node, ast.With):
        for item in node.items:
            if item.optional_vars is not None and _derives(item.context_expr, tainted):
                _bind(item.optional_vars, tainted)


def _tainted_names(function: _Function, *, is_reader_method: bool) -> set[str]:
    tainted: set[str] = set()
    if is_reader_method:
        tainted |= {a.arg for a in function.args.args if a.arg not in ("self", "cls")}
    # 代入の連鎖を辿るため、増えなくなるまで回す
    for _ in range(4):
        before = len(tainted)
        for node in ast.walk(function):
            _propagate(node, tainted)
        if len(tainted) == before:
            break
    return tainted


def _derives(expr: ast.AST, tainted: set[str]) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in tainted)
        or (isinstance(n, ast.Call) and _call_name(n) in _READ_SOURCES)
        for n in ast.walk(expr)
    )


def _read_side(
    function: _Function,
    name: str,
    path: str,
    owner: ast.ClassDef | None,
    vocab: _Vocabulary,
) -> _Side | None:
    is_reader_method = function.name in _READ_METHODS and owner is not None
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    if not is_reader_method and not any(_call_name(c) in _READ_SOURCES for c in calls):
        # 読み込みの入口が無い関数は、何も辿れない
        return None
    tainted = _tainted_names(function, is_reader_method=is_reader_method)
    is_csv = any(_call_name(c) == "DictReader" for c in calls)
    side = _Side(name, path, kind=StoreFormat.CSV if is_csv else StoreFormat.JSON)
    for node in ast.walk(function):
        if isinstance(node, ast.Subscript) and _derives(node.value, tainted):
            side.add(vocab.key(node.slice, path))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("get", "pop", "setdefault")
                and node.args
                and _derives(func.value, tainted)
            ):
                side.add(vocab.key(node.args[0], path))
            built = _call_name(node)
            takes = any(_derives(a, tainted) for a in node.args) or any(
                _derives(k.value, tainted) for k in node.keywords
            )
            if takes and (built[:1].isupper() or built == "cls"):
                type_name = owner.name if built == "cls" and owner is not None else built
                # 例外やライブラリの型を除くため、リポジトリ内のクラスに解けたものだけを数える
                if type_name != "cls" and (
                    built == "cls" or vocab.find(vocab.classes, path, type_name) is not None
                ):
                    side.types.add(type_name)
                spread = any(k.arg is None and _derives(k.value, tainted) for k in node.keywords)
                if spread and (fields := vocab.find(vocab.classes, path, type_name)) is not None:
                    side.keys |= fields[1]
        elif isinstance(node, ast.Compare) and any(_derives(c, tainted) for c in node.comparators):
            side.add(vocab.key(node.left, path))
    return side if side.keys or side.types else None


def _sink_argument(call: ast.Call, sink: _Sink) -> ast.expr | None:
    if sink.index < len(call.args):
        return call.args[sink.index]
    return next((k.value for k in call.keywords if k.arg == sink.parameter), None)


def _called_sinks(
    function: _Function, calls: list[ast.Call], path: str, vocab: _Vocabulary
) -> dict[int, _Sink]:
    # 自分自身を呼ぶ再帰は、見出しの出どころにならない
    return {
        id(c): sink[1]
        for c in calls
        if _call_name(c) != function.name
        and (sink := vocab.find_called(vocab.sinks, path, c)) is not None
        and sink[1] is not None
    }


def _write_kinds(
    function: _Function, calls: list[ast.Call], sinks: dict[int, _Sink]
) -> set[StoreFormat]:
    kinds = {
        StoreFormat.CSV
        if _call_name(c) in ("writerow", "writerows", "DictWriter")
        else StoreFormat.JSON
        for c in calls
        if _call_name(c) in _WRITE_CALLS
    } | {s.kind for s in sinks.values()}
    if function.name in _WRITE_METHODS:
        kinds.add(StoreFormat.JSON)
    return kinds


def _add_written_keys(
    node: ast.Call,
    side: _Side,
    function: _Function,
    path: str,
    owner: ast.ClassDef | None,
    *,
    vocab: _Vocabulary,
    sinks: dict[int, _Sink],
) -> None:
    called = _call_name(node)
    if called == "asdict" and owner is not None:
        side.keys |= _fields_of(owner)
    if called in ("DictWriter", "writerow", "writerows"):
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            side.add(*_expr_keys(arg, function, path, vocab, 0, indirect=called != "writerows"))
    if id(node) in sinks and (passed := _sink_argument(node, sinks[id(node)])) is not None:
        side.add(*_expr_keys(passed, function, path, vocab, 0, indirect=True))


def _write_side(
    function: _Function,
    name: str,
    path: str,
    owner: ast.ClassDef | None,
    vocab: _Vocabulary,
) -> _Side | None:
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    sinks = _called_sinks(function, calls, path, vocab)
    kinds = _write_kinds(function, calls, sinks)
    if not kinds:
        return None
    side = _Side(
        name, path, kind=StoreFormat.CSV if StoreFormat.CSV in kinds else StoreFormat.JSON
    )
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            side.add(*(vocab.key(key, path) for key in node.keys if key is not None))
        elif isinstance(node, ast.Call):
            _add_written_keys(node, side, function, path, owner, vocab=vocab, sinks=sinks)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
            side.add(vocab.key(node.targets[0].slice, path))
    return side if side.keys else None


def _sink_of(function: _Function, owner: ast.ClassDef | None) -> _Sink | None:
    params = [a.arg for a in [*function.args.posonlyargs, *function.args.args]]
    if owner is not None and params and params[0] in ("self", "cls"):
        params = params[1:]
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        called = _call_name(node)
        if called in ("writerow", "dump", "dumps"):
            carried = node.args[:1]
            kind = StoreFormat.CSV if called == "writerow" else StoreFormat.JSON
        elif called == "DictWriter":
            carried = [k.value for k in node.keywords if k.arg == "fieldnames"] or node.args[1:2]
            kind = StoreFormat.CSV
        else:
            continue
        if carried and isinstance(carried[0], ast.Name) and carried[0].id in params:
            return _Sink(carried[0].id, params.index(carried[0].id), kind)
    return None


def _functions(tree: ast.Module) -> list[tuple[_Function, ast.ClassDef | None]]:
    owners: dict[int, ast.ClassDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners[id(member)] = node
    return [
        (node, owners.get(id(node)))
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _vocabulary(parsed: dict[str, ast.Module]) -> _Vocabulary:
    vocab = _Vocabulary()
    modules = _Modules(parsed)
    for path, tree in parsed.items():
        vocab.imports[path] = _imports_of(tree, path, modules)
        vocab.constants[path] = _constants(tree)
        vocab.sequences[path] = {
            node.targets[0].id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, (ast.List, ast.Tuple))
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        vocab.enums[path] = {}
        vocab.classes[path] = {}
        for member in ast.walk(tree):
            if isinstance(member, ast.ClassDef):
                vocab.classes[path][member.name] = _fields_of(member)
                # 列挙型でない型も空のメンバーで載せる。受け口と同じ理由
                vocab.enums[path][member.name] = _Enum(member.name, _enum_members(member))
        vocab.functions[path] = {}
        vocab.sinks[path] = {}
        for function, owner in _functions(tree):
            vocab.functions[path].setdefault(function.name, []).append(function)
            sink = _sink_of(function, owner)
            if sink is not None or function.name not in vocab.sinks[path]:
                vocab.sinks[path][function.name] = sink
    return vocab


def _side(side: _Side, role: StoreRole) -> StoreSide:
    return StoreSide(
        role,
        side.function,
        side.path,
        side.kind,
        frozenset(side.keys),
        frozenset(side.types),
        frozenset(side.labels),
    )


def store_accesses(sources: Mapping[str, str]) -> list[StoreAccess]:
    """関数ごとの保存物の書く側と読む側を、ファイルの並びの順に返す。"""
    parsed: dict[str, ast.Module] = {}
    for path, text in sources.items():
        try:
            parsed[path] = ast.parse(text)
        except (SyntaxError, RecursionError, MemoryError, ValueError):
            # 構文を読めないファイルは、保存物の手がかりを拾えないので飛ばす。
            # 入れ子の深い式は RecursionError、巨大な入力は MemoryError、
            # NUL 文字は ValueError で落ちる
            continue
    vocab = _vocabulary(parsed)
    found: list[StoreAccess] = []
    for path, tree in parsed.items():
        for function, owner in _functions(tree):
            qualified = f"{owner.name}.{function.name}" if owner is not None else function.name
            writer = _write_side(function, qualified, path, owner, vocab)
            reader = _read_side(function, qualified, path, owner, vocab)
            sides = tuple(
                _side(side, role)
                for side, role in ((writer, StoreRole.WRITE), (reader, StoreRole.READ))
                if side is not None
            )
            if not sides:
                # 保存物に触れない関数は載せない
                continue
            paired = owner is not None and function.name in (_WRITE_METHODS | _READ_METHODS)
            found.append(
                StoreAccess(
                    path,
                    frozenset(_store_names(function, path, vocab)),
                    owner.name if paired and owner is not None else None,
                    sides,
                )
            )
    return found
