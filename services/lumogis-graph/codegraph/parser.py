# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tree-sitter source parser → language-agnostic ``ParsedModule`` (LUM-301).

Direct AST traversal (stable ``node.type`` / ``child_by_field_name`` API)
rather than ``.scm`` query files — robust across grammar versions and fully
unit-testable. Grammars come from the bundled per-language wheels
(``tree-sitter-python`` / ``tree-sitter-typescript``), which need no network
(unlike ``tree-sitter-language-pack``, which fetches grammars at runtime).

The parser knows nothing about the graph; it only turns one file's bytes into
``Symbol`` / ``CallSite`` / ``ImportRef`` records (incl. a synthetic
``kind="module"`` symbol for the file itself, used as the import anchor).
"""

from __future__ import annotations

import logging

from codegraph.models import CallSite
from codegraph.models import ImportRef
from codegraph.models import ParsedModule
from codegraph.models import Symbol
from codegraph.models import make_qualname
from codegraph.models import module_prefix

_log = logging.getLogger(__name__)

# Recursion bound for the AST walk (security hardening — deeply-nested source).
_MAX_AST_DEPTH = 500

_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript_tsx",
    ".mts": "typescript",
    ".cts": "typescript",
}


def lang_for_path(path: str) -> str | None:
    """Return the v1 language token for a file path, or None if unsupported."""
    lower = path.lower()
    for ext, lang in _EXT_LANG.items():
        if lower.endswith(ext):
            return lang
    return None


def _get_parser(lang: str):
    """Build a tree-sitter Parser from a bundled grammar wheel (no network)."""
    from tree_sitter import Language
    from tree_sitter import Parser

    if lang == "python":
        import tree_sitter_python as ts

        return Parser(Language(ts.language()))
    if lang in ("typescript", "typescript_tsx"):
        import tree_sitter_typescript as ts

        fn = ts.language_tsx if lang == "typescript_tsx" else ts.language_typescript
        return Parser(Language(fn()))
    raise ValueError(f"unsupported language {lang!r}")


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _ident_name(node, src: bytes) -> str | None:
    """Name from a class/function definition node (the `name` field)."""
    name_node = node.child_by_field_name("name")
    return _text(name_node, src) if name_node is not None else None


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
_PY_DEF = {"function_definition", "class_definition"}


def _py_bases(class_node, src: bytes) -> tuple[str, ...]:
    supers = class_node.child_by_field_name("superclasses")  # argument_list
    if supers is None:
        return ()
    out: list[str] = []
    for child in supers.named_children:
        if child.type == "identifier":
            out.append(_text(child, src))
        elif child.type == "attribute":
            attr = child.child_by_field_name("attribute")
            if attr is not None:
                out.append(_text(attr, src))
    return tuple(out)


def _py_callee(call_node, src: bytes) -> str | None:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(fn, src)
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return _text(attr, src) if attr is not None else None
    return None


def _py_imports(root, src: bytes, file_rel: str) -> list[ImportRef]:
    own_top = file_rel.strip("/").split("/", 1)[0]
    out: list[ImportRef] = []

    def visit(node):
        if node.type == "import_statement":
            for child in node.named_children:
                if child.type in ("dotted_name", "aliased_import"):
                    target = child
                    if child.type == "aliased_import":
                        target = child.child_by_field_name("name") or child
                    mod = _text(target, src)
                    out.append(_mk_import(mod, own_top, file_rel, relative=False))
        elif node.type == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            if mod_node is not None:
                mod = _text(mod_node, src)
                relative = mod.startswith(".") or mod_node.type == "relative_import"
                out.append(_mk_import(mod.lstrip("."), own_top, file_rel, relative))
        for child in node.children:
            visit(child)

    visit(root)
    return out


def _mk_import(module: str, own_top: str, file_rel: str, relative: bool) -> ImportRef:
    # P2-5: best-effort first-party heuristic for v1 — relative import, or shares
    # this file's TOP path segment. This assumes a single top-level package per
    # root; a monorepo with multiple top-level packages under one root can
    # mislabel (a sibling top package reads as external -> spurious Library).
    # Longer term, resolve against the set of ingested module qualnames (LUM-535).
    first_party = relative or (module.split(".", 1)[0] == own_top if module else False)
    return ImportRef(module=module, is_external=not first_party, file=file_rel)


# --------------------------------------------------------------------------- #
# TypeScript
# --------------------------------------------------------------------------- #
_TS_DEF = {
    "function_declaration": "function",
    "method_definition": "method",
    "class_declaration": "class",
}


def _ts_bases(class_node, src: bytes) -> tuple[str, ...]:
    out: list[str] = []
    for child in class_node.named_children:
        if child.type == "class_heritage":
            for clause in child.named_children:
                # extends_clause / implements_clause → type identifiers
                for t in clause.named_children:
                    name = t.child_by_field_name("name")
                    out.append(_text(name if name is not None else t, src).split(".")[-1])
    return tuple(out)


def _ts_callee(call_node, src: bytes) -> str | None:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(fn, src)
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        return _text(prop, src) if prop is not None else None
    return None


def _ts_imports(root, src: bytes, file_rel: str) -> list[ImportRef]:
    out: list[ImportRef] = []

    def visit(node):
        if node.type == "import_statement":
            source = node.child_by_field_name("source")
            if source is not None:
                spec = _text(source, src).strip("\"'`")
                # Relative/absolute path specifiers are first-party; bare ones external.
                external = not (spec.startswith(".") or spec.startswith("/"))
                out.append(ImportRef(module=spec, is_external=external, file=file_rel))
        for child in node.children:
            visit(child)

    visit(root)
    return out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
class CodeParser:
    """Parse one source file into a ``ParsedModule``. Stateless across calls."""

    def parse_file(self, src: bytes, file_rel_posix: str) -> ParsedModule:
        lang_tok = lang_for_path(file_rel_posix)
        if lang_tok is None:
            raise ValueError(f"unsupported file type: {file_rel_posix}")
        norm_lang = "python" if lang_tok == "python" else "typescript"
        parser = _get_parser(lang_tok)
        tree = parser.parse(src)
        root = tree.root_node

        symbols: list[Symbol] = []
        calls: list[CallSite] = []

        # Synthetic module-level Component anchors imports and module-scope calls.
        mod_qual = module_prefix(file_rel_posix, norm_lang)
        symbols.append(
            Symbol(
                name=mod_qual.rsplit(".", 1)[-1] or mod_qual,
                qualname=mod_qual,
                kind="module",
                file=file_rel_posix,
                start_line=1,
                end_line=max(1, root.end_point[0] + 1),
                signature=None,
                lang=norm_lang,
            )
        )

        defs = _PY_DEF if norm_lang == "python" else set(_TS_DEF)
        callee_fn = _py_callee if norm_lang == "python" else _ts_callee
        bases_fn = _py_bases if norm_lang == "python" else _ts_bases

        def walk(node, nesting: list[str], depth: int = 0):
            # Bound recursion: pathological/deeply-nested source must not blow the
            # Python stack (the ingest layer would skip the file, but cap here so a
            # hostile/generated file degrades to a truncated parse, not RecursionError).
            if depth > _MAX_AST_DEPTH:
                return
            enclosing = make_qualname(file_rel_posix, norm_lang, nesting) if nesting else mod_qual
            if node.type == "call" or node.type == "call_expression":
                callee = callee_fn(node, src)
                if callee:
                    calls.append(
                        CallSite(
                            caller_qualname=enclosing,
                            callee_name=callee,
                            file=file_rel_posix,
                            line=node.start_point[0] + 1,
                        )
                    )
            is_def = node.type in defs
            child_nesting = nesting
            if is_def:
                name = _ident_name(node, src)
                if name:
                    if norm_lang == "python":
                        kind = "class" if node.type == "class_definition" else _py_kind(node)
                    else:
                        kind = _TS_DEF[node.type]
                    qual = make_qualname(file_rel_posix, norm_lang, [*nesting, name])
                    is_class = node.type in ("class_definition", "class_declaration")
                    bases = bases_fn(node, src) if is_class else ()
                    symbols.append(
                        Symbol(
                            name=name,
                            qualname=qual,
                            kind=kind,
                            file=file_rel_posix,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=_signature(node, src),
                            lang=norm_lang,
                            bases=bases,
                        )
                    )
                    child_nesting = [*nesting, name]
            for child in node.children:
                walk(child, child_nesting, depth + 1)

        walk(root, [])

        if norm_lang == "python":
            imports = _py_imports(root, src, file_rel_posix)
        else:
            imports = _ts_imports(root, src, file_rel_posix)

        return ParsedModule(
            file=file_rel_posix,
            lang=norm_lang,
            symbols=tuple(symbols),
            calls=tuple(calls),
            imports=tuple(imports),
        )


def _py_kind(node) -> str:
    """A function_definition is a 'method' iff lexically inside a class body."""
    parent = node.parent
    while parent is not None:
        if parent.type == "class_definition":
            return "method"
        if parent.type == "function_definition":
            return "function"
        parent = parent.parent
    return "function"


def _signature(node, src: bytes) -> str | None:
    """Callable signature `name(params)`, or None for non-callables (classes).

    P3-1: a class has a `name` but no `parameters` field, so without this guard
    the "signature" was just the class name (a redundant duplicate of `name`).
    Return None when there are no parameters — signature is a callable concept.
    """
    params = node.child_by_field_name("parameters")
    name = node.child_by_field_name("name")
    if name is None or params is None:
        return None
    return (_text(name, src) + _text(params, src))[:512]
