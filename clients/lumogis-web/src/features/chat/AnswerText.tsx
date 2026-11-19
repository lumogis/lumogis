// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Lumogis's answers, formatted (LUM-993 follow-up). Models answer in Markdown;
// showing it raw put "**2,400**" on screen. This renders the common subset —
// paragraphs, headings, bold, italics, inline code, code blocks, bullet and
// numbered lists, links — as React elements. It never creates HTML from the
// text, so nothing in an answer can run as code; links open only for http(s).
// Unfinished syntax while an answer streams in simply shows as text.

import type { ReactNode } from "react";

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "h"; level: number; text: string }
  | { kind: "code"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

/** Split an answer into blocks: blank lines separate paragraphs; fences, lists and headings are their own blocks. */
function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (/^\s*```/.test(line)) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i]!)) body.push(lines[i++]!);
      i += 1; // closing fence (or end of text while streaming)
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "h", level: heading[1]!.length, text: heading[2]! });
      i += 1;
      continue;
    }
    const bullet = /^\s*[-*+]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const kind = bullet.test(line) ? "ul" : "ol";
      const marker = kind === "ul" ? bullet : numbered;
      const items: string[] = [];
      while (i < lines.length && marker.test(lines[i]!)) {
        let item = lines[i]!.replace(marker, "");
        i += 1;
        // An indented continuation line belongs to the item above.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]!) && !bullet.test(lines[i]!) && !numbered.test(lines[i]!)) {
          item += ` ${lines[i]!.trim()}`;
          i += 1;
        }
        items.push(item);
      }
      blocks.push({ kind, items });
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i]!.trim() !== "" &&
      !/^\s*```/.test(lines[i]!) &&
      !/^#{1,6}\s/.test(lines[i]!) &&
      !bullet.test(lines[i]!) &&
      !numbered.test(lines[i]!)
    ) {
      para.push(lines[i]!);
      i += 1;
    }
    blocks.push({ kind: "p", lines: para });
  }
  return blocks;
}

const SAFE_LINK = /^https?:\/\//i;

/** Inline formatting: `code`, **bold**, *italic* / _italic_, [text](url). */
function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${keyPrefix}-${n++}`;
    if (m[1] !== undefined) out.push(<code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{m[1]}</code>);
    else if (m[2] !== undefined || m[3] !== undefined) out.push(<strong key={key}>{renderInline((m[2] ?? m[3])!, key)}</strong>);
    else if (m[4] !== undefined || m[5] !== undefined) out.push(<em key={key}>{renderInline((m[4] ?? m[5])!, key)}</em>);
    else if (m[6] !== undefined && m[7] !== undefined) {
      out.push(
        SAFE_LINK.test(m[7]) ? (
          <a key={key} href={m[7]} target="_blank" rel="noopener noreferrer" className="text-accent-text underline underline-offset-2">
            {renderInline(m[6], key)}
          </a>
        ) : (
          // Not http(s): show the text, drop the target (no javascript: or data: links).
          <span key={key}>{renderInline(m[6], key)}</span>
        ),
      );
    }
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function AnswerText({ text }: { text: string }): JSX.Element {
  const blocks = parseBlocks(text);
  return (
    <div className="flex flex-col gap-3">
      {blocks.map((b, i) => {
        const key = `b${i}`;
        switch (b.kind) {
          case "code":
            return (
              <pre key={key} className="m-0 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs leading-5 whitespace-pre">
                <code>{b.text}</code>
              </pre>
            );
          case "h":
            // Answer headings are emphasis, not page structure: no h1–h6 inside the transcript.
            return (
              <p key={key} className="m-0 font-semibold">
                {renderInline(b.text, key)}
              </p>
            );
          case "ul":
          case "ol": {
            const List = b.kind;
            return (
              <List key={key} className={`m-0 flex flex-col gap-1 pl-5 ${b.kind === "ul" ? "list-disc" : "list-decimal"}`}>
                {b.items.map((item, j) => (
                  <li key={`${key}-${j}`}>{renderInline(item, `${key}-${j}`)}</li>
                ))}
              </List>
            );
          }
          default:
            return (
              <p key={key} className="m-0">
                {b.lines.map((l, j) => (
                  <span key={`${key}-${j}`}>
                    {j > 0 ? <br /> : null}
                    {renderInline(l, `${key}-${j}`)}
                  </span>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
