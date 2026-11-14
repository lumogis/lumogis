# ADR-222: Global CSS classes in Lumogis Web are `lumogis-`-namespaced, and an undefined unprefixed class fails the test suite (LUM-852)
**Status:** Finalised (canonical copy: `docs/decisions/222-lum-852-dead-css-classes.md`)
**Created:** 2026-11-06
**Last updated:** 2026-11-06
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-852`); finalised by claude-fable-5-1 (`/verify-plan --headless LUM-852`)
**Linear:** LUM-852

## Context

Lumogis Web styles the DOM through one global stylesheet (`clients/lumogis-web/src/design/tokens.css`) and five CSS modules. Nothing checks that a `className` string matches a rule, and jsdom applies no CSS, so a wrong class does nothing and every gate stays green. LUM-852 found two cases:

- `sr-only`, a Tailwind/Bootstrap name, on the privacy-mode and reranking `<legend>`s. They render visibly, repeating the heading above them.
- `lumogis-documents__empty`, later cited as the precedent for re-aligning `EmptyState`. It was never defined.

Measurement on 2026-11-06:

- All **342** global classes defined in `tokens.css` begin with `lumogis-`.
- **207** distinct class tokens used in TSX have no rule.
  - **11** are unprefixed, and all of those are defects or un-namespaced hooks.
  - **196** are `lumogis-*`, mostly deliberate BEM hooks; 154 of them are on the email surface.

A guard requiring every class to be defined would therefore fail 207 times on day one.

## Decision

Every global class in Lumogis Web is `lumogis-`-prefixed. A Vitest test, `tests/design/classNames.test.ts`, parses every `src/**/*.tsx` file with the TypeScript compiler API (`typescript` is already a direct devDependency, so no dependency is added) and collects the class tokens of `className` attributes: attribute strings, string literals anywhere inside a `className={…}` expression (ternary branches, `&&` operands, array elements), and the literal segments of template literals, skipping any fragment that touches `${`. String literals that are call arguments, comparison operands or element-access keys are excluded. It fails on any token that is **not** `lumogis-`-prefixed and **not** defined in a global stylesheet.

The CSS side of the convention is asserted, not assumed: a second assertion in the same file fails if `tokens.css` defines any class that is not `lumogis-`-prefixed. The test carries non-vacuity floors (minimum file, token and defined-class counts, plus a known-good class at a known site) and in-file scanner fixtures, so the mutation proof that was run once at implementation is permanent. LUM-852 fixes all current hits:

- The legends move to `lumogis-visually-hidden`.
- The `lumogis-documents__empty` prop is dropped, because `EmptyState` centres on purpose on this page.
- `text-sm text-muted` becomes a real `lumogis-document-chat__context-used` rule.
- The unstyled `safety-*` / `email-search*` hooks are renamed into the namespace.

No new dependency is added.

## Alternatives Considered

The full detail is in `.cursor/explorations/LUM-852-dead-css-classes.md`.

- **Strict "every class defined":** 207 failures on day one.
- **Ratchet baseline of all undefined classes:** catches the `lumogis-documents__empty` shape too, but fires on every new BEM hook. Deferred until the email-surface styling question is answered.
- **`eslint-plugin-undefined-css-classes`:** v0.1.5, last release 2025-10, zero adoption. Configured sensibly it equals the chosen test, at the cost of a dependency.
- **`scan-react-css`:** pulls a second TypeScript (^5.9 against the pinned ~5.6) and LESS.
- **Playwright DOM-vs-stylesheet check:** coverage depends on which pages a spec visits, and it is not in default CI.
- **CSS Modules migration:** out of proportion.
- **Reverse-direction tools (stylelint-no-unused-selectors, Biome `noUnusedClasses`, PurgeCSS):** answer the wrong question.
- **Regex scanner (the exploration's prototype):** replaced at planning by the compiler API, which tells ternary branches, call arguments and template segments apart by node kind instead of by pattern.

## Consequences

- **Easier:** a borrowed utility name (`sr-only`, `text-muted`, …) can no longer ship silently. The namespace convention becomes machine-checked on both sides, use and definition, rather than tribal.
- **Not covered, deliberately:**
  - a mistyped or never-styled `lumogis-*` class;
  - classes assembled in variables or helpers (`btnClass`, `buttonClassName()`), pass-through `className={className}` and JSX spread;
  - string literals that are call arguments inside `className={…}`. This keeps `.join(" ")` quiet, but a future `clsx("sr-only")` / `cn(…)` helper would bypass the guard. None exists today; adopting one means teaching the scanner its name;
  - CSS-module members (`styles.x`), which Vite types as an index signature;
  - selectors that mention a class but no longer match (the LUM-282 two-class descendant rule).

  The test header lists these so nobody reads green as proof of more.
- **Future chunks:** a new global class must be `lumogis-`-prefixed. A non-prefixed class is allowed only if `tokens.css` defines it, which the CSS-side assertion makes impossible in practice. Visual results, such as a legend actually being hidden, still need a browser check. jsdom cannot prove them.

## Revisit conditions

- The email-surface styling evaluation concludes the `lumogis-*` hooks **should** be styled → adopt the ratchet baseline (exploration Option 2) for `lumogis-*` tokens.
- A second regression of the `lumogis-documents__empty` shape (an undefined `lumogis-*` class that someone believed did something) reaches `dev` → adopt the ratchet regardless.
- `tokens.css` is split into several files or moves to CSS Modules → the scanner's stylesheet set must follow, or the guard becomes vacuous.
- A `clsx` / `cn`-style class helper is adopted → the scanner must learn its name, or the call-argument exclusion silently exempts every class passed through it.
- `eslint-plugin-undefined-css-classes` (or a successor) reaches a maintained 1.x with real adoption → consider swapping in for editor-time feedback.

## Status history
- 2026-11-06: Draft created by /explore (headless, run-workflow LUM-852)
- 2026-11-06: **Finalised by `/verify-plan`** — implementation confirmed the decision. Two amendments recorded from the plan: the scanner parses TSX with the TypeScript compiler API rather than regex, and the CSS-side prefix invariant is asserted by the test (`U5b`) rather than stated as convention. The call-argument blind spot (a future `clsx`/`cn` helper) is added to the consequences and revisit conditions.
