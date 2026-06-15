// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/**
 * Constrained markdown → HTML for lumogis.ai static pages.
 * Escape-then-tag: no raw HTML passthrough from source.
 */

const TICKET_ID_RE = /\*\*?LUM-\d+\*\*?|\bLUM-\d+\b/g;

export function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function stripTicketIds(text) {
  return String(text).replace(TICKET_ID_RE, "").replace(/  +/g, " ").trim();
}

function renderInline(text, { stripTicketIds: stripIds = false } = {}) {
  let src = stripIds ? stripTicketIds(text) : text;
  const parts = [];
  const re =
    /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match;
  while ((match = re.exec(src)) !== null) {
    if (match.index > last) {
      parts.push(escapeHtml(src.slice(last, match.index)));
    }
    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(`<strong>${escapeHtml(token.slice(2, -2))}</strong>`);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(`<code>${escapeHtml(token.slice(1, -1))}</code>`);
    } else if (token.startsWith("[")) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (linkMatch) {
        const label = escapeHtml(linkMatch[1]);
        const href = escapeHtml(linkMatch[2]);
        parts.push(`<a href="${href}">${label}</a>`);
      } else {
        parts.push(escapeHtml(token));
      }
    } else {
      parts.push(escapeHtml(token));
    }
    last = match.index + token.length;
  }
  if (last < src.length) {
    parts.push(escapeHtml(src.slice(last)));
  }
  return parts.join("");
}

function closeList(state, out) {
  if (state.listType === "ul") {
    out.push("</ul>");
  }
  state.listType = null;
}

function closeBlockquote(state, out) {
  if (state.inBlockquote) {
    out.push("</blockquote>");
    state.inBlockquote = false;
  }
}

/**
 * @param {string} markdown
 * @param {{ stripTicketIds?: boolean }} [options]
 * @returns {string}
 */
export function renderMarkdown(markdown, options = {}) {
  const lines = String(markdown).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  const state = { listType: null, inBlockquote: false };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (trimmed === "") {
      closeList(state, out);
      closeBlockquote(state, out);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
    if (heading) {
      closeList(state, out);
      closeBlockquote(state, out);
      const level = heading[1].length;
      const tag = `h${level}`;
      out.push(`<${tag}>${renderInline(heading[2], options)}</${tag}>`);
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      closeList(state, out);
      if (!state.inBlockquote) {
        out.push("<blockquote>");
        state.inBlockquote = true;
      }
      const quoteText = trimmed.replace(/^>\s?/, "");
      out.push(`<p>${renderInline(quoteText, options)}</p>`);
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      closeBlockquote(state, out);
      if (state.listType !== "ul") {
        closeList(state, out);
        out.push("<ul>");
        state.listType = "ul";
      }
      const item = trimmed.replace(/^[-*]\s+/, "");
      out.push(`<li>${renderInline(item, options)}</li>`);
      continue;
    }

    closeList(state, out);
    closeBlockquote(state, out);
    out.push(`<p>${renderInline(trimmed, options)}</p>`);
  }

  closeList(state, out);
  closeBlockquote(state, out);
  return out.join("\n");
}

/**
 * Drop changelog ## [Unreleased] section (through next ## [version] or EOF).
 * @param {string} markdown
 * @returns {string}
 */
export function omitUnreleasedSection(markdown) {
  const text = String(markdown);
  const marker = /^## \[Unreleased\]\s*$/m;
  const match = marker.exec(text);
  if (!match) {
    return text;
  }
  const start = match.index;
  const rest = text.slice(start + match[0].length);
  const nextVersion = /^## \[[^\]]+\]/m.exec(rest);
  const end = nextVersion ? start + match[0].length + nextVersion.index : text.length;
  const before = text.slice(0, start).trimEnd();
  const after = nextVersion ? rest.slice(nextVersion.index).trimStart() : "";
  return [before, after].filter(Boolean).join("\n\n");
}

/**
 * True when changelog has no version sections after optional unreleased strip.
 * @param {string} markdown
 */
export function changelogHasNoReleases(markdown) {
  return !/^## \[[^\]]+\]/m.test(markdown);
}
