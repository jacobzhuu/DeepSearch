import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { reportMarkdownComponents } from './reportMarkdownComponents';
import { sanitizeReportMarkdown } from './reportMarkdownDisplay';

const C1 = '844e09e5-1d1d-4bfc-9e02-7e5831527cfc';
const C2 = '99ab174f-7ea2-4d04-bcb2-051f5f50538f';
const C3 = 'd719b318-b37f-4189-88f7-782d1993c1c2';

describe('sanitizeReportMarkdown', () => {
  it('removes legacy evidence span and leaves no <span', () => {
    const raw = `## 证据脚注\n\n[^e1]: <span id="evidence-${C2}"></span>**https://openai.com/x** — 摘录: "hello"\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).not.toMatch(/<span/i);
    expect(out).toContain('https://openai.com/x');
    expect(out).toContain('[^e1]:');
    expect(out).toContain('## 证据来源');
  });

  it('unwraps span-wrapped URLs', () => {
    const raw = `[^e1]: <span>https://openai.com/a/b</span> — ok\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).not.toMatch(/<span/i);
    expect(out).toContain('https://openai.com/a/b');
  });

  it('collapses Chinese triple-UUID ledger line to single claim_evidence', () => {
    const raw = `## 证据脚注\n\n[^e1]: [x](<https://x>) — 摘录: "q" — 账本 id: \`${C1}\` · \`${C2}\` · \`${C3}\`\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).not.toMatch(/ · `/);
    expect(out).toContain('追溯键:');
    expect(out).not.toContain('claim_evidence');
    expect(out).toContain(`\`${C2}\``);
    expect(out).not.toContain(C1);
    expect(out).not.toContain(C3);
  });

  it('collapses English triple-UUID ledger line', () => {
    const raw = `## Evidence footnotes\n\n[^e1]: [x](<https://x>) — excerpt: "q" — Ledger ids: \`${C1}\` · \`${C2}\` · \`${C3}\`\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).toContain('Trace key:');
    expect(out).not.toContain('claim_evidence');
    expect(out).toContain(`\`${C2}\``);
    expect(out).not.toContain(C1);
  });

  it('collapses parenthesized backtick triple to trace key', () => {
    const raw = `## 证据脚注\n\n[^e1]: **u** — 摘录: "x" (\`${C1}\` / \`${C2}\` / \`${C3}\`)\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).toContain('追溯键:');
    expect(out).not.toContain('claim_evidence');
    expect(out).toContain(`\`${C2}\``);
    expect(out).not.toMatch(/\([^)]*\/[^)]*\/[^)]*\)/);
  });

  it('collapses plain parenthesized UUID triple', () => {
    const raw = `## 证据脚注\n\n[^e1]: hi (${C1} / ${C2} / ${C3})\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).toContain(`\`${C2}\``);
    expect(out).not.toContain(`(${C1}`);
  });

  it('strips legacy claim_evidence wording from trace label', () => {
    const raw = `## 证据来源\n\n[^e1]: x — 追溯键（claim_evidence）：\`${C2}\`\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).toContain('追溯键:');
    expect(out).not.toContain('claim_evidence');
  });

  it('English-only markdown has no Chinese evidence section heading', () => {
    const raw = `## Evidence footnotes\n\n[^e1]: ok\n`;
    const out = sanitizeReportMarkdown(raw);
    expect(out).not.toContain('## 证据来源');
    expect(out).not.toContain('## 证据脚注');
  });
});

describe('reportMarkdownComponents', () => {
  it('suppresses GFM footnote-label h2 so markup has no Footnotes text', () => {
    const md = `## 证据来源\n\nBody[^e1].\n\n[^e1]: foot.\n`;
    const html = renderToStaticMarkup(
      React.createElement(ReactMarkdown, {
        remarkPlugins: [remarkGfm],
        components: reportMarkdownComponents,
        children: md,
      }),
    );
    expect(html).not.toContain('Footnotes');
    expect(html).toContain('证据来源');
  });
});
