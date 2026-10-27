from dataclasses import dataclass

import pytest

from jigane.flaky.catalog import load_catalog
from jigane.flaky.detector import detect_source
from jigane.gates.line_scope import AllLines
from jigane.languages.base import LanguageAdapter
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter

CATALOG = load_catalog()
UNDETERMINED = "宛先を確かめられない"
REMOTE = "宛先がローカルの外（example.com）"


@dataclass(frozen=True)
class Case:
    name: str
    language: str
    path: str
    source: str
    # None は検出なし。(警告か, 理由) は FLK-NETWORK の検出
    expected: tuple[bool, str] | None


ADAPTERS: dict[str, LanguageAdapter] = {
    "python": PythonAdapter(),
    "java": JavaAdapter(),
    "csharp": CSharpAdapter(),
    "swift": SwiftAdapter(),
}

CSHARP_CLASS = """public class FooTests
{
    private static Uri RouteUri(RunningListener listener, string pathAndQuery) =>
        new($"https://127.0.0.1:{listener.Endpoint.Port}{pathAndQuery}");

    private static string Host() => "https://example.com";

    private const string Base = "http://localhost:5000/x";

    [Fact]
    public async Task Calls()
    {
        var client = new HttpClient();
        await client.%s;
    }
}
"""
JAVA_CLASS = """class FooTest {
    static final String BASE = "http://localhost:8080/x";
    static String route(String p) { return "http://127.0.0.1:" + port + p; }

    @Test
    void connects(String injected) throws Exception {
        %s
    }
}
"""
PYTHON_MODULE = """import requests

BASE = "http://localhost:8000/x"


def route(p):
    return f"http://127.0.0.1:{PORT}{p}"


def test_a(injected):
    %s
"""
SWIFT_CLASS = """import XCTest

final class NetworkTests: XCTestCase {
    static let base = URL(string: "http://localhost:8080/x")!
    func route(_ p: String) -> URL { URL(string: "http://127.0.0.1:\\(port)\\(p)")! }

    func testFetch(injected: URL) async throws {
        %s
    }
}
"""

PY = ("python", "tests/test_x.py", PYTHON_MODULE)
JAVA = ("java", "src/test/FooTest.java", JAVA_CLASS)
CS = ("csharp", "tests/FooTests.cs", CSHARP_CLASS)
SWIFT = ("swift", "Tests/NetworkTests.swift", SWIFT_CLASS)


def _case(
    name: str, kind: tuple[str, str, str], call: str, expected: tuple[bool, str] | None
) -> Case:
    language, path, template = kind
    return Case(name, language, path, template % call, expected)


CASES = [
    _case("py-loopback", PY, 'requests.get("http://localhost:1/x")', None),
    _case("py-remote", PY, 'requests.get("https://example.com/x")', (False, REMOTE)),
    _case("py-helper", PY, 'requests.get(route("/a"))', None),
    _case("py-const", PY, "requests.get(BASE)", None),
    _case("py-param", PY, "requests.get(injected)", (True, UNDETERMINED)),
    _case(
        "py-query-localhost",
        PY,
        'requests.get("https://evil.example/?next=localhost")',
        (False, "宛先がローカルの外（evil.example）"),
    ),
    _case(
        "py-localhost-subdomain",
        PY,
        'requests.get("https://localhost.evil.example/x")',
        (False, "宛先がローカルの外（localhost.evil.example）"),
    ),
    _case("py-ipv4-loopback", PY, 'requests.get("http://127.0.0.1:8080/x")', None),
    _case("py-ipv6-loopback", PY, 'requests.get("http://[::1]/x")', None),
    _case("java-loopback", JAVA, 'new Socket("localhost", 80);', None),
    _case("java-remote", JAVA, 'new Socket("example.com", 80);', (False, REMOTE)),
    _case("java-helper", JAVA, 'new URL(route("/a")).openConnection();', None),
    _case("java-const", JAVA, "new URL(BASE).openConnection();", None),
    _case("java-param", JAVA, "new URL(injected).openConnection();", (True, UNDETERMINED)),
    _case("cs-loopback", CS, 'GetAsync("http://localhost:5000/api")', None),
    _case("cs-remote", CS, 'GetAsync("https://example.com/api")', (False, REMOTE)),
    _case("cs-helper", CS, "PostAsync(RouteUri(listener, path), content: null)", None),
    _case("cs-helper-remote", CS, "GetAsync(Host())", (False, REMOTE)),
    _case("cs-const", CS, "GetAsync(Base)", None),
    _case("cs-param", CS, "GetAsync(uri)", (True, UNDETERMINED)),
    _case("cs-other-file-helper", CS, "GetAsync(Elsewhere.Url(path))", (True, UNDETERMINED)),
    _case(
        "swift-loopback",
        SWIFT,
        'URLSession.shared.data(from: URL(string: "http://localhost:1/x")!)',
        None,
    ),
    _case(
        "swift-remote",
        SWIFT,
        'URLSession.shared.data(from: URL(string: "https://example.com/x")!)',
        (False, REMOTE),
    ),
    _case("swift-helper", SWIFT, 'URLSession.shared.data(from: route("/a"))', None),
    _case("swift-const", SWIFT, "URLSession.shared.data(from: Self.base)", None),
    _case("swift-param", SWIFT, "URLSession.shared.data(from: injected)", (True, UNDETERMINED)),
    _case(
        "py-ternary-local-or-remote",
        PY,
        'requests.get("http://localhost/x" if flag else "https://example.com/x")',
        (False, REMOTE),
    ),
    _case("py-uppercase-localhost", PY, 'requests.get("http://LOCALHOST:8080/x")', None),
    _case(
        "py-ternary-two-local",
        PY,
        'requests.get("http://localhost/x" if flag else "http://127.0.0.1/x")',
        None,
    ),
    _case(
        "py-ternary-local-or-unresolvable",
        PY,
        'requests.get("http://localhost/x" if flag else "http:///x")',
        (True, UNDETERMINED),
    ),
    _case(
        "cs-ternary-local-or-remote",
        CS,
        'GetAsync(flag ? "http://localhost:5000/a" : "https://example.com/a")',
        (False, REMOTE),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_network_target(case: Case) -> None:
    found = detect_source(
        case.path, case.source.encode(), ADAPTERS[case.language], CATALOG, AllLines()
    )
    network = [(d.undetermined, d.reason) for d in found if d.item_id == "FLK-NETWORK"]
    assert network == ([] if case.expected is None else [case.expected])
