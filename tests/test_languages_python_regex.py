import pytest

from jigane.languages.base import RegexAdapter, RegexDialect, RegexScan, RegexUse, parse
from jigane.languages.python import PythonAdapter

adapter = PythonAdapter()


def scan(code: str) -> RegexScan:
    source = code.encode()
    return adapter.regex_literals(parse(adapter, source).root_node, source, "a.py")


def patterns(code: str) -> list[tuple[str, str]]:
    return [(found.call, found.pattern) for found in scan(code).literals]


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(adapter, RegexAdapter)


@pytest.mark.parametrize(
    "function",
    ["compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"],
)
def test_each_function_is_picked(function: str) -> None:
    assert patterns(f"import re\nre.{function}('a+', x)\n") == [(f"re.{function}", "a+")]


def test_pattern_keyword_is_picked() -> None:
    assert patterns("import re\nre.sub(repl='x', pattern='a', string=s)\n") == [("re.sub", "a")]


def test_dialect_path_and_line() -> None:
    found = scan("import re\n\nre.compile('a')\n").literals[0]
    assert found.dialect is RegexDialect.PYTHON
    assert found.path == "a.py"
    assert found.line == 3


def test_module_alias_is_normalised() -> None:
    assert patterns("import re as r\nr.sub('a', '', s)\n") == [("re.sub", "a")]


def test_from_import_with_and_without_alias() -> None:
    code = "from re import sub, split as sp\nsub('a', '', s)\nsp(',+', s)\n"
    assert patterns(code) == [("re.sub", "a"), ("re.split", ",+")]


def test_other_module_named_re_is_ignored() -> None:
    assert patterns("import regex as re\nre.compile('a')\n") == []


def test_unrelated_calls_are_ignored() -> None:
    code = (
        "import re\n"
        "re.escape('a.b')\n"
        "re.compile()\n"
        "other.compile('a')\n"
        "'x'.split(',')\n"
        "sub('a', '', s)\n"
    )
    assert patterns(code) == []
    assert scan(code).composed == ()


def test_from_import_of_other_module_is_ignored() -> None:
    assert patterns("from regex import sub\nsub('a', '', s)\n") == []


def test_non_pattern_functions_of_re_from_import_are_ignored() -> None:
    assert patterns("from re import escape\nescape('a')\n") == []


def test_document_order_with_nested_calls() -> None:
    code = "import re\nre.sub('a', re.sub('b', '', s), t)\nre.match('c', s)\n"
    assert patterns(code) == [("re.sub", "a"), ("re.sub", "b"), ("re.match", "c")]


def test_adjacent_and_plus_concatenation() -> None:
    code = "import re\nre.compile('a' 'b')\nre.compile('c' + 'd')\nre.compile(('e' 'f') + 'g')\n"
    assert [p for _, p in patterns(code)] == ["ab", "cd", "efg"]


def test_raw_string_keeps_backslashes() -> None:
    assert patterns("import re\nre.compile(r'\\d+\\.')\n") == [("re.compile", "\\d+\\.")]


def test_escapes_are_decoded() -> None:
    assert patterns("import re\nre.compile('\\\\d\\t')\n") == [("re.compile", "\\d\t")]


def test_triple_quoted_string() -> None:
    assert patterns('import re\nre.compile("""a\n  b""")\n') == [("re.compile", "a\n  b")]


def test_bytes_pattern_is_decoded() -> None:
    assert patterns("import re\nre.compile(b'a+')\n") == [("re.compile", "a+")]


def test_composed_patterns() -> None:
    code = (
        "import re\n"
        "re.compile(name)\n"
        "re.compile(f'{x}+')\n"
        "re.search('a' + name, s)\n"
        "re.match('%s' % x, s)\n"
        "re.sub('a' f'{x}', '', s)\n"
    )
    found = scan(code)
    assert found.literals == ()
    assert [(c.call, c.line) for c in found.composed] == [
        ("re.compile", 2),
        ("re.compile", 3),
        ("re.search", 4),
        ("re.match", 5),
        ("re.sub", 6),
    ]


def test_literals_and_composed_are_separate() -> None:
    found = scan("import re\nre.compile('a')\nre.compile(x)\n")
    assert [lit.pattern for lit in found.literals] == ["a"]
    assert [c.line for c in found.composed] == [3]


def test_uses() -> None:
    code = (
        "import re\n"
        "re.compile('a')\nre.match('a', s)\nre.search('a', s)\nre.fullmatch('a', s)\n"
        "re.findall('a', s)\nre.finditer('a', s)\nre.sub('a', 'b', s)\nre.subn('a', 'b', s)\n"
        "re.split('a', s)\nre.sub(x, 'b', s)\n"
    )
    result = scan(code)
    assert [(r.call, r.use) for r in result.literals] == [
        ("re.compile", RegexUse.COMPILE),
        ("re.match", RegexUse.MATCH),
        ("re.search", RegexUse.MATCH),
        ("re.fullmatch", RegexUse.MATCH),
        ("re.findall", RegexUse.MATCH),
        ("re.finditer", RegexUse.MATCH),
        ("re.sub", RegexUse.REPLACE),
        ("re.subn", RegexUse.REPLACE),
        ("re.split", RegexUse.SPLIT),
    ]
    assert [(r.call, r.use) for r in result.composed] == [("re.sub", RegexUse.REPLACE)]


def test_local_constant_is_followed_one_step() -> None:
    code = 'import re\n\ndef f(s):\n    p = "a+" "b"\n    return re.match(p, s)\n'
    found = scan(code)
    assert [(x.call, x.pattern) for x in found.literals] == [("re.match", "a+b")]
    assert found.composed == ()


@pytest.mark.parametrize(
    "body",
    [
        '    p = "a"\n    p = "b"\n    return re.match(p, s)\n',
        '    p = "a"\n    p += "b"\n    return re.match(p, s)\n',
        '    if s:\n        p = "a"\n    return re.match(p, s)\n',
        "    for p in s:\n        pass\n    return re.match(p, s)\n",
        '    p = "a"\n    p = q = "b"\n    return re.match(p, s)\n',
        '    p = q = "a"\n    return re.match(p, s)\n',
        '    return re.match(p, s)\n    p = "a"\n',
        '    q = "a"\n    p = q\n    return re.match(p, s)\n',
    ],
)
def test_local_constant_is_not_followed(body: str) -> None:
    found = scan(f"import re\n\ndef f(s):\n{body}")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_parameter_is_not_followed() -> None:
    found = scan("import re\n\ndef f(p):\n    return re.match(p, 'x')\n")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_f_string_without_interpolation_is_composed() -> None:
    # literal_eval が f 文字列を受け付けないため、補間が無くても定数にならない
    found = scan("import re\nre.compile(f'a+')\n")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_comment_between_adjacent_strings_is_skipped() -> None:
    code = "import re\nre.compile(('a'  # note\n    'b'))\n"
    assert patterns(code) == [("re.compile", "ab")]
