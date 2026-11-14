# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the tree-sitter code parser (LUM-301).

Pure parsing — no graph, no FalkorDB. Exercises the public ``CodeParser``
surface for Python and TypeScript and the error-tolerance contract.
"""

from codegraph.parser import CodeParser
from codegraph.parser import lang_for_path


def test_python_functions_and_calls():
    src = b"import os\n\n\ndef a():\n    return b()\n\n\ndef b():\n    return 1\n"
    pm = CodeParser().parse_file(src, "pkg/mod.py")

    names = {s.name: s for s in pm.symbols}
    assert "a" in names and "b" in names
    assert names["a"].kind == "function"
    assert names["a"].qualname == "pkg.mod.a"
    # module-level synthetic symbol present
    assert any(s.kind == "module" and s.qualname == "pkg.mod" for s in pm.symbols)
    # call a -> b captured with the enclosing caller qualname
    call = next(c for c in pm.calls if c.callee_name == "b")
    assert call.caller_qualname == "pkg.mod.a"
    # `os` is an absolute import not sharing this file's top package → external
    assert any(i.module == "os" and i.is_external for i in pm.imports)


def test_typescript_class_methods():
    src = b"import {x} from './util';\nclass C extends D {\n  m() { return f(); }\n}\n"
    pm = CodeParser().parse_file(src, "src/c.ts")
    kinds = {s.name: s.kind for s in pm.symbols}
    assert kinds.get("C") == "class"
    assert kinds.get("m") == "method"
    # extends target captured as a base for IMPLEMENTS mapping
    cls = next(s for s in pm.symbols if s.name == "C")
    assert "D" in cls.bases
    # relative import './util' is first-party
    assert any(i.module == "./util" and not i.is_external for i in pm.imports)
    # method call f() captured
    assert any(c.callee_name == "f" for c in pm.calls)


def test_method_qualname_nesting():
    src = b"class A:\n    def run(self):\n        return 1\n"
    pm = CodeParser().parse_file(src, "a.py")
    run = next(s for s in pm.symbols if s.name == "run")
    assert run.kind == "method"
    assert run.qualname == "a.A.run"


def test_unparseable_file_does_not_raise():
    # tree-sitter is error-tolerant; a broken file parses to a tree with ERROR
    # nodes rather than raising. (Ingest decides whether to skip.)
    pm = CodeParser().parse_file(b"def (((:\n  ???\n", "broken.py")
    assert pm.file == "broken.py"
    assert pm.lang == "python"


def test_lang_detection():
    assert lang_for_path("a.py") == "python"
    assert lang_for_path("a.ts") == "typescript"
    assert lang_for_path("a.tsx") == "typescript_tsx"
    assert lang_for_path("a.md") is None
