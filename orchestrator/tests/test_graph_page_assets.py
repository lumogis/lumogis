# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph pages load vendored, same-origin scripts only (LUM-941).

Proves, across Core and the graph service trees:

* no household-served legacy page (Core ``static/`` and ``dashboard/``, the
  graph service ``static/``) references a third-party origin in its markup or
  CSS;
* the vendored bytes match their SHA-256 pins and the two vendor trees are
  identical;
* every ``<script src>`` is an allowlisted ``/graph/assets/`` file whose
  ``integrity`` matches the committed bytes.

Limit: the guard inspects markup and CSS, not JavaScript. A script that builds a
URL at runtime (``s.src = ...``, ``fetch(...)``, ``import(...)``) is not
detected; test ``test_only_allowlisted_script_sources`` limits ``<script src>``
to the allowlist, and the offline browser proof recorded at verify time is the
runtime check.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAGE_ROOTS = (
    "orchestrator/static",
    "orchestrator/dashboard",
    "services/lumogis-graph/static",
)
_VENDOR_DIRS = (
    "orchestrator/static/vendor",
    "services/lumogis-graph/static/vendor",
)
_PINNED_SHA256 = {
    "cytoscape-3.28.1.min.js": ("92d752b48ea949720675865197fd2a0001c95bc5888545e990af60321712d4c6"),
    "alpine-3.14.8.min.js": ("b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643"),
}

_CSS_URL = re.compile(r"url\(\s*['\"]?\s*(?:https?:)?//", re.IGNORECASE)
_CSS_IMPORT = re.compile(
    r"@import\s+(?:url\(\s*)?['\"]?\s*(?:https?:)?//",
    re.IGNORECASE,
)
_CSS_URL_VALUE = re.compile(
    r"url\(\s*['\"]?\s*((?:https?:)?//[^'\")\s]+)",
    re.IGNORECASE,
)
_CSS_IMPORT_VALUE = re.compile(
    r"@import\s+(?:url\(\s*)?['\"]?\s*((?:https?:)?//[^'\")\s;]+)",
    re.IGNORECASE,
)


class _AssetRefs(HTMLParser):
    """Collect every attribute that makes a browser load or follow an external URL.

    ``src`` on any element, every URL in ``srcset``, ``<link href>``,
    ``<object data>``, ``<base href>`` and the ``style`` attribute of any
    element, plus the text of ``<style>`` blocks. ``<a href>`` is navigation,
    not a load, and is not collected.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str, str, dict[str, str | None]]] = []
        self.css: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "style":
            self._in_style = True
        for attr, value in attrs:
            if value is None:
                continue
            if attr == "src":
                self.refs.append((tag, attr, value, d))
            elif attr == "srcset":
                for part in value.split(","):
                    url = part.strip().split(" ")[0]
                    if url:
                        self.refs.append((tag, attr, url, d))
            elif attr == "href" and tag in ("link", "base"):
                self.refs.append((tag, attr, value, d))
            elif attr == "data" and tag == "object":
                self.refs.append((tag, attr, value, d))
            elif attr == "style":
                self.css.append(value)

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


def _parse(html: str) -> _AssetRefs:
    p = _AssetRefs()
    p.feed(html)
    p.close()
    return p


def external_refs(html: str) -> list[str]:
    """Every third-party URL the markup or CSS would make a browser fetch."""
    p = _parse(html)
    out: list[str] = []
    for _tag, _attr, value, _d in p.refs:
        v = value.strip()
        if v.lower().startswith(("http:", "https:", "//")):
            out.append(v)
    for css in p.css:
        if _CSS_URL.search(css) or _CSS_IMPORT.search(css):
            out.extend(_CSS_URL_VALUE.findall(css))
            out.extend(_CSS_IMPORT_VALUE.findall(css))
    return list(dict.fromkeys(out))  # `@import url(...)` matches both CSS patterns


def _external_refs_in_page(path: Path) -> list[str]:
    return external_refs(path.read_text(encoding="utf-8"))


def _sri_sha384(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def _pages() -> list[Path]:
    pages: list[Path] = []
    for root in _PAGE_ROOTS:
        pages.extend(sorted((_REPO_ROOT / root).rglob("*.html")))
    return pages


def _tree_of(page: Path) -> str:
    rel = page.relative_to(_REPO_ROOT).as_posix()
    return "services/lumogis-graph" if rel.startswith("services/lumogis-graph") else "orchestrator"


# 1 ------------------------------------------------------------------------


def test_page_roots_exist():
    """A missing root fails (never skips) so the guard can't pass vacuously."""
    for rel in (*_PAGE_ROOTS, *_VENDOR_DIRS):
        assert (_REPO_ROOT / rel).is_dir(), f"{rel} missing under {_REPO_ROOT}"


# 2 ------------------------------------------------------------------------


def test_no_household_page_loads_external_assets():
    pages = _pages()
    assert len(pages) >= 5, f"expected >= 5 pages, scanned {len(pages)}"
    bad = [
        f"{p.relative_to(_REPO_ROOT)}: {url}" for p in pages for url in _external_refs_in_page(p)
    ]
    assert bad == []


# 3 ------------------------------------------------------------------------


def test_external_refs_detects_cdn_loads():
    """Negative control: the parser really sees every kind of third-party load."""
    html = (
        '<script src="https://cdn.example/x.js"></script>'
        '<script src="//cdn.example/y.js"></script>'
        '<link rel="stylesheet" href="HTTP://cdn.example/z.css">'
        '<style>@import url("https://fonts.example/a.css");</style>'
        '<img src="https://img.example/p.png">'
        '<img srcset="/a.png 1x, https://img.example/b.png 2x">'
        "<style>@font-face{src:URL(HTTPS://fonts.example/f.woff2)}</style>"
        '<div style="background:url(//img.example/bg.png)"></div>'
        '<object data="https://obj.example/o.svg"></object>'
        '<base href="https://base.example/">'
        # The pre-change tags from graph_mgm.html, verbatim.
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/'
        'cytoscape.min.js" crossorigin="anonymous"></script>'
        '<script src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js" defer>'
        "</script>"
    )
    found = external_refs(html)
    for needle in (
        "https://cdn.example/x.js",
        "//cdn.example/y.js",
        "HTTP://cdn.example/z.css",
        "https://fonts.example/a.css",
        "https://img.example/p.png",
        "https://img.example/b.png",
        "HTTPS://fonts.example/f.woff2",
        "//img.example/bg.png",
        "https://obj.example/o.svg",
        "https://base.example/",
        "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js",
        "https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js",
    ):
        assert needle in found, f"{needle} not detected; got {found}"
    assert len(found) == 12

    clean = (
        '<script src="/graph/assets/a.js"></script>'
        '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E">'
        "<button onclick=\"location.href='http://localhost:3080'\">x</button>"
    )
    assert external_refs(clean) == []


# 4 ------------------------------------------------------------------------


def test_vendor_dirs_are_byte_exact_in_gitattributes():
    lines = (_REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for vendor in _VENDOR_DIRS:
        rule = [ln.split() for ln in lines if ln.split()[:1] == [f"{vendor}/**"]]
        assert rule, f".gitattributes has no rule for {vendor}/**"
        assert "-text" in rule[0], f"{vendor}/** must be -text (byte-exact for SRI)"


# 5 ------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", _VENDOR_DIRS)
@pytest.mark.parametrize("name", sorted(_PINNED_SHA256))
def test_vendored_files_match_pins(vendor, name):
    data = (_REPO_ROOT / vendor / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == _PINNED_SHA256[name]


# 6 ------------------------------------------------------------------------


def test_vendor_dirs_are_identical():
    a, b = (_REPO_ROOT / d for d in _VENDOR_DIRS)
    names_a = sorted(p.name for p in a.iterdir())
    names_b = sorted(p.name for p in b.iterdir())
    assert names_a == names_b
    assert set(names_a) == {*_PINNED_SHA256, "LICENSES.md"}
    for n in names_a:
        assert (a / n).read_bytes() == (b / n).read_bytes(), f"{n} differs between trees"


# 7 ------------------------------------------------------------------------


def test_graph_mgm_copies_identical():
    core = (_REPO_ROOT / "orchestrator/static/graph_mgm.html").read_bytes()
    kg = (_REPO_ROOT / "services/lumogis-graph/static/graph_mgm.html").read_bytes()
    assert core == kg


# 8 ------------------------------------------------------------------------


def test_every_vendored_script_tag_has_matching_integrity():
    checked = 0
    for page in _pages():
        for tag, attr, value, attrs in _parse(page.read_text(encoding="utf-8")).refs:
            if tag != "script" or attr != "src" or not value.startswith("/graph/assets/"):
                continue
            name = value.rsplit("/", 1)[1]
            assert name in _PINNED_SHA256, f"{page}: unknown asset {name}"
            vendor = _REPO_ROOT / _tree_of(page) / "static" / "vendor" / name
            assert attrs.get("integrity") == _sri_sha384(vendor.read_bytes()), (
                f"{page.relative_to(_REPO_ROOT)}: integrity does not match {name}"
            )
            expected = {"src", "integrity"} | ({"defer"} if name.startswith("alpine") else set())
            assert set(attrs) == expected, (
                f"{page.relative_to(_REPO_ROOT)}: {name} tag attributes {sorted(attrs)}"
            )
            checked += 1
    assert checked >= 5, f"only {checked} vendored script tags checked"


# 9 ------------------------------------------------------------------------


def _literal_assign(tree: ast.Module, name: str):
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return node.value
    pytest.fail(f"module-level assignment {name} not found in graph routes/mgm.py")


def test_route_allowlists_match_vendor_dir():
    from routes import admin

    assert set(admin._GRAPH_ASSETS) == set(_PINNED_SHA256)

    src = (_REPO_ROOT / "services/lumogis-graph/routes/mgm.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    assets = _literal_assign(tree, "_ASSETS")
    assert isinstance(assets, ast.Call) and getattr(assets.func, "id", None) == "frozenset"
    assert len(assets.args) == 1 and isinstance(assets.args[0], ast.Set)
    assert set(ast.literal_eval(assets.args[0])) == set(_PINNED_SHA256)

    headers = _literal_assign(tree, "_ASSET_HEADERS")
    assert isinstance(headers, ast.Dict)
    assert ast.literal_eval(headers) == admin._GRAPH_ASSET_HEADERS


# 10 -----------------------------------------------------------------------


def test_licenses_notice_names_both_packages():
    text = (_REPO_ROOT / _VENDOR_DIRS[0] / "LICENSES.md").read_text(encoding="utf-8")
    for needle in (
        "## cytoscape 3.28.1",
        "## alpinejs 3.14.8",
        "The Cytoscape Consortium",
        "Caleb Porzio",
        "AGPL-3.0-only",
        "alpinejs/alpine/v3.14.8/LICENSE.md",
        *_PINNED_SHA256.values(),
        _sri_sha384((_REPO_ROOT / _VENDOR_DIRS[0] / "cytoscape-3.28.1.min.js").read_bytes()),
        _sri_sha384((_REPO_ROOT / _VENDOR_DIRS[0] / "alpine-3.14.8.min.js").read_bytes()),
    ):
        assert needle in text, f"LICENSES.md lacks {needle!r}"
    assert text.count("Permission is hereby granted") >= 2
    for cdn in ("cdnjs", "jsdelivr", "unpkg"):
        assert cdn not in text


# 11 -----------------------------------------------------------------------


def test_only_allowlisted_script_sources():
    for page in _pages():
        text = page.read_text(encoding="utf-8")
        rel = page.relative_to(_REPO_ROOT)
        for tag, attr, value, _attrs in _parse(text).refs:
            if tag == "script" and attr == "src":
                assert value.startswith("/graph/assets/"), f"{rel}: script src {value}"
                assert value.rsplit("/", 1)[1] in _PINNED_SHA256, f"{rel}: script src {value}"
        for banned in ("loadCytoscapeLocal", "loadAlpineLocal", "/static/"):
            assert banned not in text, f"{rel} still contains {banned}"


# 12 -----------------------------------------------------------------------


class _CloakOutsideAlpine(HTMLParser):
    """Collect ``x-cloak`` elements that sit outside every ``x-data`` subtree."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[bool] = []  # per open element: inside an x-data subtree?
        self.stray: list[tuple[str, int]] = []

    def handle_starttag(self, tag, attrs):
        names = {a for a, _ in attrs}
        inside = (bool(self._stack) and self._stack[-1]) or "x-data" in names
        if "x-cloak" in names and not inside:
            self.stray.append((tag, self.getpos()[0]))
        if tag not in self._VOID:
            self._stack.append(inside)

    def handle_endtag(self, tag):
        if tag not in self._VOID and self._stack:
            self._stack.pop()


def test_x_cloak_only_inside_alpine_roots():
    """Alpine clears ``x-cloak`` only on elements it initialises (an ``x-data`` root
    and its subtree). ``<body x-cloak>`` outside the root stayed ``display:none``
    for good, so the page was blank even with both scripts loaded (found by the
    LUM-941 offline browser proof).
    """
    for page in _pages():
        p = _CloakOutsideAlpine()
        p.feed(page.read_text(encoding="utf-8"))
        p.close()
        assert p.stray == [], f"{page.relative_to(_REPO_ROOT)}: x-cloak outside x-data {p.stray}"
