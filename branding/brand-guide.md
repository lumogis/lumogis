# Lumogis Brand Guide

## The Mark — Lum Node Constellation

One dominant central node (the reasoning point) connected by thin lines to three smaller nodes at asymmetric positions. The upper-right satellite also connects downward to a lower-right trailing node, creating the sense of an expanding, unfinished graph — part of something larger.

```
  •      •
   \    /
    •          <- central (bright)
        \
         •
```

The asymmetry is intentional. Do not regularise it.

---

## Files

| File | Use |
|---|---|
| `logo.svg` | Default horizontal lockup (light backgrounds) |
| `logo-dark.svg` | Horizontal lockup for dark backgrounds |
| `logo-icon.svg` | Mark only — app icons, favicons, embeds |
| `logo.png` | Raster horizontal lockup (1200 × 600) |
| `favicon-16x16.png` | Browser tab favicon |
| `favicon-32x32.png` | Browser tab favicon (HiDPI) |
| `apple-touch-icon.png` | iOS home screen (180 × 180) |
| `icon-512.png` | PWA / splash screens |

---

## Colors

| Token | Hex | Usage |
|---|---|---|
| `--brand-primary` | `#E8632A` | Central node, CTA buttons, links |
| `--brand-light` | `#F0965A` | Satellite nodes, hover states |
| `--brand-pale` | `#FDE8D8` | Inner node highlight, backgrounds |
| `--brand-glow` | `#E8632A` at 12–18% opacity | Soft radial glow only |
| `--text-dark` | `#18120E` | Wordmark on light, body text |
| `--text-light` | `#FAFAF9` | Wordmark on dark, inverted text |
| `--text-muted` | `#6B5E56` | Captions, secondary labels |

---

## Typography

**Wordmark font:** [Geist](https://vercel.com/font) — weight 300 (Light), letter-spacing +1.5px, lowercase only.  
Fallback stack: `'Geist', 'Inter', 'DM Sans', ui-sans-serif, system-ui, sans-serif`

| Role | Font | Weight | Notes |
|---|---|---|---|
| Wordmark | Geist | 300 Light | Lowercase, tracked |
| UI headings | Geist | 400–500 | Sentence case |
| Body text | Geist | 400 | 16px base, 1.6 line-height |
| Code / monospace | Geist Mono | 400 | All technical strings |

---

## Usage rules

**Do:**
- Use `logo.svg` on white or very light warm backgrounds
- Use `logo-dark.svg` on backgrounds darker than #888
- Give the mark a clear zone equal to the height of the central node on all sides
- Scale the SVG freely — it is resolution-independent

**Do not:**
- Recolour the mark to anything other than the brand amber palette
- Separate the wordmark from the mark except in documented icon-only contexts
- Add drop shadows, outlines, or gradients to the mark
- Rotate or skew the constellation
- Uppercase or title-case "lumogis"

---

## Minimum sizes

| Context | Minimum width |
|---|---|
| Horizontal lockup | 120px |
| Icon mark only | 16px (favicon) |
| Print | 30mm |
