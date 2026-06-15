# Lumogis Brand Guidelines

*March 2026 · Single source of truth for all brand assets*

---

## Logo Mark

The Lumogis mark is a **node constellation** — a dominant central node connected to four asymmetric satellite nodes by thin lines. It represents a knowledge graph: one primary intelligence connected to a personal data ecosystem.

### Files

| File | Use |
|---|---|
| `logo-mark.svg` | Transparent background — use on any coloured surface |
| `logo-mark-dark.svg` | Dark background with glow effect |
| `logo-horizontal-light.svg` | Horizontal lockup on white |
| `logo-horizontal-dark.svg` | Horizontal lockup on dark |
| `logo-stacked-light.svg` | Stacked lockup on white |
| `logo-stacked-dark.svg` | Stacked lockup on dark |
| `github-social-preview.svg` | 1280×640 — upload at GitHub → Settings → General → Social preview |
| `readme-banner.svg` | 1280×320 — top of README: `![Lumogis](branding/readme-banner.svg)` |
| `favicon.svg` | 32×32 — browser tab, lumogis.com/lumogis.ai |

---

## Colour Palette

| Name | Hex | Use |
|---|---|---|
| Amber | `#F5A623` | Primary — central node, highlights, CTAs |
| Amber Light | `#FFCC77` | Satellite nodes, gradient start |
| Dark | `#0D0D0F` | Primary background |
| Dark Surface | `#1E1E22` | Cards, elevated surfaces |
| Grey | `#888888` | Connection lines (light bg), body text, secondary labels |
| Grey Dark | `#444444` | Connection lines (dark bg) |
| White | `#FFFFFF` | Wordmark on dark, body text on dark |

---

## Typography

Two fonts only — no third typeface, no runtime Google Fonts. All UI surfaces self-host woff2 files locally.

### Syne — Wordmark & Headings

- **Syne SemiBold (600)** — subheadings, nav emphasis
- **Syne Bold (700)** — wordmark, section headings
- **Syne ExtraBold (800)** — hero headings, large display text
- Letter spacing: `-0.02em` at large sizes
- Self-hosted woff2 (weights 600, 700, 800)

### Martian Mono — Body, Labels, UI, Code

- **Martian Mono Light (300)** — taglines, secondary text
- **Martian Mono Regular (400)** — body copy, descriptions
- **Martian Mono Medium (500)** — labels, badges, UI elements
- **Martian Mono SemiBold (600)** / **Bold (700)** — emphasis, strong UI
- Body/prose: letter-spacing `-0.03em`, line-height `~1.6`, size ~1px smaller than a typical sans (15px base)
- UI labels: letter-spacing `-0.02em`
- Self-hosted woff2 (weights 300–700; variable font acceptable when available)

### Retired

- **DM Mono** — replaced by Martian Mono (March 2026 typography refresh)

---

## Usage Rules

### Do
- Use the dark background version as the primary
- Maintain the square aspect ratio of the mark at all times
- Use Syne for all heading-level text
- Use Martian Mono for body, labels, UI, counts, and code
- Keep a minimum clear space of 0.5× the mark width on all sides

### Don't
- Don't stretch or skew the mark
- Don't use the mark below 32×32px — use the favicon (central node only) instead
- Don't place the amber mark on a yellow or light orange background
- Don't use any fonts other than Syne and Martian Mono in official assets
- Don't add drop shadows to the mark on light backgrounds

### Minimum sizes

| Context | Minimum size |
|---|---|
| Full lockup (mark + wordmark) | 120px wide |
| Mark only | 24px |
| Favicon (central node only) | 16px |

---

## Taglines

**Primary:**
> The AI comes to your data. Not the other way around.

**Short form:**
> local · private · yours

**Technical:**
> Privacy is not a setting here. It is the architecture.

---

## README Usage

Add the banner to the top of `README.md`:

```markdown
![Lumogis](branding/readme-banner.svg)
```

---

## GitHub Social Preview

Upload `branding/github-social-preview.svg` (convert to PNG first if GitHub rejects SVG):

**GitHub → Repository → Settings → General → Social preview → Edit → Upload image**

Recommended: open the SVG in a browser, screenshot at 1280×640, save as PNG.

---

*Lumogis · Brand Guidelines*
