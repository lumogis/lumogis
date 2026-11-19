// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Class-name namespace guard (LUM-852).
 *
 * THE CONVENTION: every global CSS class in Lumogis Web is `lumogis-`-prefixed.
 * A `className` token that is NOT prefixed and is NOT defined in a global
 * stylesheet is a class borrowed from another framework (`sr-only`, `text-sm`)
 * that silently does nothing. This test fails on it. A second assertion checks
 * that every class the global stylesheet defines is prefixed, so the premise
 * the guard relies on is verified rather than assumed.
 *
 * WHAT IS CHECKED: `className` JSX attributes in `src/**\/*.tsx`. That covers
 * string attributes and string literals anywhere inside a `className={…}`
 * expression (ternary branches, `&&` operands, array elements). It also covers
 * the literal segments of template literals; a token touching `${`, on either
 * side, is a fragment of a built name and is skipped.
 *
 * EXCLUDED INSIDE EXPRESSIONS: string literals that are call arguments
 * (`.join(" ")`, `.replace("_", "-")`), comparison operands
 * (`===`, `!==`, `==`, `!=`) and element-access keys.
 *
 * KNOWN LIMITS. A green run does NOT prove any of these:
 *  - a mistyped or never-styled `lumogis-*` class (the `lumogis-documents__empty`
 *    shape: prefixed, so it passes, but no rule exists);
 *  - classes built in variables or helpers (`btnClass`, `buttonClassName()`,
 *    pass-through `className={className}`, JSX spread `{...props}`);
 *  - string literals passed as ARGUMENTS TO ANY CALL inside `className={…}`.
 *    That exclusion keeps `.join(" ")` quiet, but it also means a future
 *    `clsx("sr-only")` / `cn(…)` helper would bypass the guard. None exists
 *    today. If one is adopted, the scanner must learn its name;
 *  - CSS-module members (`styles.x`, typed by Vite as an index signature);
 *  - a selector that mentions a class but no longer matches (the LUM-282
 *    descendant rule).
 * Visual results (a legend actually being hidden) need a browser: jsdom
 * applies no CSS.
 *
 * TAILWIND (LUM-993, amends ADR 222): an unprefixed token is also accepted when
 * Tailwind, compiled from the app's own `src/design/ui.css` (theme included),
 * generates a rule for it. So `text-sm` passes now because it really styles
 * something, while a misspelt `text-smm` or a class from a framework we do not
 * load still fails. Tailwind's marker classes (`group`, `peer`, `group/name`,
 * `peer/name`) generate no rule of their own and are accepted by name.
 *
 * CLASS HELPERS (LUM-993, ADR 222's revisit condition): `cn(…)` and `cva(…)` are
 * class-name helpers, so their string arguments ARE scanned, wherever the call
 * is (inside `className={…}` or at module level, in `.tsx` and `.ts` files).
 * For `cva(base, config)` the scan covers `base`, every value under
 * `config.variants`, and the `class` / `className` of each `compoundVariants`
 * entry; `defaultVariants` holds variant names, not classes, and is skipped.
 * Object-literal arguments of any other call (`buttonVariants({ variant: "x" })`)
 * are variant names and are skipped too. Classes held in a variable and passed
 * to `cn(variable)` are still unscanned.
 *
 * MAINTENANCE: if `tokens.css` is split, or another global stylesheet is
 * imported in `main.tsx`, add it to `GLOBAL_STYLESHEETS`. Otherwise the guard
 * reports false violations (the safe direction); the floors below catch the
 * opposite failure, a scan that quietly matches nothing.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { compile } from "@tailwindcss/node";
import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const clientRoot = path.join(__dirname, "..", "..");

const GLOBAL_STYLESHEETS = ["src/design/tokens.css"] as const;
const PREFIX = "lumogis-";

// Non-vacuity floors (at plan time: 135 files / 788 tokens / 342 defined).
// LUM-993 plan B Pass 8 removed the old page CSS: tokens.css now defines only the
// app shell's classes, so the defined floor follows it (6 at the time).
const MIN_FILES = 100;
const MIN_TOKENS = 400;
const MIN_DEFINED = 5;

interface ClassToken {
  file: string;
  line: number;
  token: string;
}

/** Class names appearing in selector preludes of a stylesheet. */
function definedClasses(css: string): Set<string> {
  const stripped = css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    // Strings go before prelude matching: a `{`, `}` or `;` inside a value
    // (`content: "}"`) would otherwise split or merge preludes, and attribute
    // selector values (`[href$=".pdf"]`) must not yield classes.
    .replace(/"[^"]*"|'[^']*'/g, '""');
  const out = new Set<string>();
  for (const m of stripped.matchAll(/([^{};]+)\{/g)) {
    const prelude = m[1].trim();
    if (prelude.startsWith("@")) continue;
    for (const c of prelude.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) out.add(c[1]);
  }
  return out;
}

/** Split text on whitespace; drop a fragment cut off by an adjacent `${`. */
function splitTokens(text: string, leftOpen: boolean, rightOpen: boolean): string[] {
  const parts = text.split(/\s+/);
  if (leftOpen && !/^\s/.test(text)) parts.shift();
  if (rightOpen && !/\s$/.test(text)) parts.pop();
  return parts.filter((p) => p.length > 0);
}

const CLASS_HELPERS = new Set(["cn", "cva"]);
/** Tailwind marker classes: they name a group or peer and generate no rule themselves. */
const MARKER = /^(group|peer)(\/[\w-]+)?$/;

function isClassHelperCall(n: ts.Node): n is ts.CallExpression {
  return ts.isCallExpression(n) && ts.isIdentifier(n.expression) && CLASS_HELPERS.has(n.expression.text);
}

function isIgnoredLiteral(node: ts.Node): boolean {
  const parent = node.parent;
  if (ts.isCallExpression(parent) && parent.arguments.some((a) => a === node)) return !isClassHelperCall(parent);
  if (ts.isPropertyAssignment(parent) && parent.name === node) return true;
  if (ts.isElementAccessExpression(parent) && parent.argumentExpression === node) return true;
  if (ts.isBinaryExpression(parent)) {
    const k = parent.operatorToken.kind;
    return (
      k === ts.SyntaxKind.EqualsEqualsToken ||
      k === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      k === ts.SyntaxKind.ExclamationEqualsToken ||
      k === ts.SyntaxKind.ExclamationEqualsEqualsToken
    );
  }
  return false;
}

function classTokens(file: string, source: string): ClassToken[] {
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
  const out: ClassToken[] = [];
  const emit = (at: ts.Node, tokens: string[]): void => {
    const line = sf.getLineAndCharacterOfPosition(at.getStart(sf)).line + 1;
    for (const token of tokens) out.push({ file, line, token });
  };

  const walk = (n: ts.Node): void => {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (!isIgnoredLiteral(n)) emit(n, splitTokens(n.text, false, false));
      return;
    }
    if (ts.isCallExpression(n) && !CLASS_HELPERS.has(ts.isIdentifier(n.expression) ? n.expression.text : "")) {
      // Another call: its string arguments are skipped (isIgnoredLiteral) and its
      // object-literal arguments hold variant names, not classes.
      walk(n.expression);
      for (const a of n.arguments) if (!ts.isObjectLiteralExpression(a)) walk(a);
      return;
    }
    if (ts.isTemplateExpression(n)) {
      emit(n.head, splitTokens(n.head.text, false, true));
      n.templateSpans.forEach((span, i) => {
        walk(span.expression);
        const last = i === n.templateSpans.length - 1;
        emit(span.literal, splitTokens(span.literal.text, true, !last));
      });
      return;
    }
    ts.forEachChild(n, walk);
  };

  const prop = (obj: ts.Node, name: string): ts.Expression | undefined => {
    if (!ts.isObjectLiteralExpression(obj)) return undefined;
    for (const p of obj.properties) {
      if (ts.isPropertyAssignment(p) && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) && p.name.text === name) {
        return p.initializer;
      }
    }
    return undefined;
  };
  const values = (obj: ts.Node | undefined): ts.Expression[] =>
    obj && ts.isObjectLiteralExpression(obj)
      ? obj.properties.filter(ts.isPropertyAssignment).map((p) => p.initializer)
      : [];
  const cvaCall = (call: ts.CallExpression): void => {
    const [base, config] = call.arguments;
    if (base) walk(base);
    if (!config) return;
    for (const group of values(prop(config, "variants"))) for (const v of values(group)) walk(v);
    const compound = prop(config, "compoundVariants");
    if (compound && ts.isArrayLiteralExpression(compound)) {
      for (const entry of compound.elements) {
        for (const key of ["class", "className"]) {
          const v = prop(entry, key);
          if (v) walk(v);
        }
      }
    }
  };

  const visit = (n: ts.Node): void => {
    if (isClassHelperCall(n)) {
      if ((n.expression as ts.Identifier).text === "cva") cvaCall(n);
      else n.arguments.forEach(walk);
      return;
    }
    if (ts.isJsxAttribute(n) && n.name.getText(sf) === "className" && n.initializer) {
      if (ts.isStringLiteral(n.initializer)) {
        emit(n.initializer, splitTokens(n.initializer.text, false, false));
      } else if (ts.isJsxExpression(n.initializer) && n.initializer.expression) {
        walk(n.initializer.expression);
      }
      return;
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return out;
}

/** `.tsx` and `.ts` sources (a `.ts` file can hold `cn`/`cva` calls); no generated code. */
function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "generated") out.push(...tsxFiles(full));
    } else if (entry.isFile() && /\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts")) out.push(full);
  }
  return out.sort();
}

function violations(tokens: ClassToken[], defined: Set<string>, tailwind: Set<string> = new Set()): string[] {
  return tokens
    .filter((t) => !t.token.startsWith(PREFIX) && !defined.has(t.token) && !tailwind.has(t.token) && !MARKER.test(t.token))
    .map((t) => `${t.file}:${t.line} ${t.token}`);
}

/** The candidates Tailwind generates a rule for, compiled from the app's ui.css. */
async function tailwindGenerated(candidates: string[]): Promise<Set<string>> {
  const base = path.join(clientRoot, "src", "design");
  const compiler = await compile(readFileSync(path.join(base, "ui.css"), "utf8"), {
    base,
    onDependency() {},
  });
  const css = compiler.build(candidates);
  // Tailwind escapes class selectors the way CSS.escape does for these characters.
  const escape = (c: string) => c.replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
  // The selector must end where the class ends: `.bg-destructiv` is a prefix of
  // `.bg-destructive` and must not count as generated.
  const generated = (c: string) => {
    const at = `.${escape(c)}`;
    for (let i = css.indexOf(at); i >= 0; i = css.indexOf(at, i + 1)) {
      if (!/[\w\\-]/.test(css.charAt(i + at.length))) return true;
    }
    return false;
  };
  return new Set(candidates.filter(generated));
}

function scanTree(): { files: string[]; tokens: ClassToken[]; defined: Set<string> } {
  const defined = new Set<string>();
  for (const sheet of GLOBAL_STYLESHEETS) {
    const css = readFileSync(path.join(clientRoot, sheet), "utf8");
    for (const c of definedClasses(css)) defined.add(c);
  }
  const files = tsxFiles(path.join(clientRoot, "src"));
  const tokens: ClassToken[] = [];
  for (const abs of files) {
    const rel = path.relative(clientRoot, abs).split(path.sep).join("/");
    tokens.push(...classTokens(rel, readFileSync(abs, "utf8")));
  }
  return { files, tokens, defined };
}

describe("class-name scanner (fixtures)", () => {
  it("U1: flags a borrowed utility name", () => {
    const tokens = classTokens("x.tsx", '<legend className="sr-only">x</legend>');
    expect(violations(tokens, new Set())).toEqual(["x.tsx:1 sr-only"]);
  });

  it("U2: skips template-literal fragments, checks whole tokens", () => {
    const a = classTokens("x.tsx", "<div className={`foo-${x} bar-thing lumogis-a--${y}`} />");
    expect(violations(a, new Set())).toEqual(["x.tsx:1 bar-thing"]);
    const b = classTokens("x.tsx", "<div className={`${a}-suffix lumogis-b`} />");
    expect(violations(b, new Set())).toEqual([]);
  });

  it("U3: checks ternaries and arrays, skips call args and comparisons", () => {
    const tokens = classTokens(
      "x.tsx",
      '<p className={[m.role === "user" ? "lumogis-a" : "oops", "lumogis-b"].join(" ")} />',
    );
    expect(violations(tokens, new Set())).toEqual(["x.tsx:1 oops"]);
  });

  it("U2b: checks the tail of the last template span and recurses into ${…}", () => {
    const tail = classTokens("x.tsx", "<div className={`${x} zed`} />");
    expect(violations(tail, new Set())).toEqual(["x.tsx:1 zed"]);
    const nested = classTokens("x.tsx", '<div className={`lumogis-a ${c ? "zed" : ""}`} />');
    expect(violations(nested, new Set())).toEqual(["x.tsx:1 zed"]);
  });

  it("U3b: skips call arguments and element-access keys", () => {
    const call = classTokens("x.tsx", '<p className={tier.replace("_", "-")} />');
    expect(violations(call, new Set())).toEqual([]);
    const access = classTokens("x.tsx", '<p className={map["zed"]} />');
    expect(violations(access, new Set())).toEqual([]);
  });

  it("U6: scans cn() and cva() arguments, but not variant names or other calls' objects", () => {
    const inAttr = classTokens("x.tsx", '<p className={cn("lumogis-a", ok && "zed", other("skip"))} />');
    expect(violations(inAttr, new Set())).toEqual(["x.tsx:1 zed"]);
    const moduleLevel = classTokens(
      "x.ts",
      [
        'const v = cva("base-zed", {',
        '  variants: { variant: { default: "lumogis-a", outline: "out-zed" }, size: { sm: "sm-zed" } },',
        '  compoundVariants: [{ variant: "outline", size: "sm", class: "comp-zed" }],',
        '  defaultVariants: { variant: "default" },',
        "});",
        'const c = cn(v({ variant: "outline" }), "tail-zed");',
      ].join("\n"),
    );
    expect(violations(moduleLevel, new Set()).map((s) => s.split(" ")[1]).sort()).toEqual(
      ["base-zed", "comp-zed", "out-zed", "sm-zed", "tail-zed"],
    );
    // Marker classes pass by name; anything else that only looks like one does not.
    const marker = classTokens("x.tsx", '<div className="group group/button peer peerish" />');
    expect(violations(marker, new Set())).toEqual(["x.tsx:1 peerish"]);
  });

  it("U4: definedClasses reads selector preludes only", () => {
    const css =
      '/* .commented */ .a, .b:hover > .c { color: red } @media (min-width: 40.5rem) { .d { x: 1 } } [href$=".pdf"] { } .e::after { content: "} .fake {" }';
    expect(definedClasses(css)).toEqual(new Set(["a", "b", "c", "d", "e"]));
  });
});

describe("class-name namespace guard (real tree)", () => {
  const { files, tokens, defined } = scanTree();
  let tailwind = new Set<string>();
  beforeAll(async () => {
    const unprefixed = [...new Set(tokens.map((t) => t.token).filter((t) => !t.startsWith(PREFIX)))];
    tailwind = await tailwindGenerated(unprefixed);
  });

  it("U5: no unprefixed undefined class, and the scan is not vacuous", () => {
    expect(files.length).toBeGreaterThanOrEqual(MIN_FILES);
    expect(tokens.length).toBeGreaterThanOrEqual(MIN_TOKENS);
    expect(defined.size).toBeGreaterThanOrEqual(MIN_DEFINED);
    expect(defined.has("lumogis-shell")).toBe(true);
    // The scan finds prefixed classes in use (the shell still has them). Not tied
    // to one class: plan B (LUM-993) moved page bodies off them pass by pass.
    expect(tokens.some((t) => t.token.startsWith(PREFIX))).toBe(true);
    expect(violations(tokens, defined, tailwind)).toEqual([]);
  });

  it("U5d: Tailwind acceptance is real, not a pass-all", async () => {
    const got = await tailwindGenerated(["text-sm", "pointer-coarse:size-11", "text-smm", "sr-onlyy", "bg-destructiv", "bg-destructive"]);
    // bg-destructiv: a prefix of a real class must not count (found at LUM-993 verify).
    expect([...got].sort()).toEqual(["bg-destructive", "pointer-coarse:size-11", "text-sm"]);
  });

  it("U5b: every defined global class is lumogis-prefixed", () => {
    expect([...defined].filter((c) => !c.startsWith(PREFIX)).sort()).toEqual([]);
  });

  it("U5c: the classes the app shell relies on are defined", () => {
    // Since LUM-993 plan B Pass 8 the shell is the only global CSS left; the
    // LUM-852 classes (lumogis-visually-hidden, the context-used strip) moved to utilities.
    for (const c of ["lumogis-shell", "lumogis-shell__body", "lumogis-shell__main", "lumogis-shell__sidebar", "lumogis-shell__bottom"]) {
      expect(defined.has(c), c).toBe(true);
    }
  });
});
