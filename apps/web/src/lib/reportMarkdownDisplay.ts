/**
 * Normalize persisted report Markdown for preview, copy, and download:
 * - Strip legacy `<span id="evidence-…">` anchors (react-markdown does not render raw HTML).
 * - Unwrap `<span>https://…</span>` URL wrappers.
 * - Remove leftover empty `<span>…</span>`.
 * - Collapse old three-UUID traces to a single claim_evidence UUID line.
 * - Normalize legacy section titles / trace labels (证据脚注 → 证据来源; drop claim_evidence wording).
 */

const LEGACY_EVIDENCE_SPAN =
  /<span\b[^>]*\bid="evidence-[0-9a-fA-F-]+"[^>]*>\s*<\/span>\s*/gi;

/** URL wrapped in a plain span (no nested tags inside). */
const URL_WRAPPED_SPAN = /<span\b[^>]*>(\s*https?:\/\/[^<\s]+)\s*<\/span>/gi;

const EMPTY_SPAN = /<span\b[^>]*>\s*<\/span>\s*/gi;

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** ` — 账本 id: `a` · `b` · `c` ` (legacy Chinese) */
const ZH_TRIPLE_LEDGER = new RegExp(
  `—\\s*账本\\s*id\\s*:\\s*\`(${UUID})\`\\s*·\\s*\`(${UUID})\`\\s*·\\s*\`(${UUID})\``,
  "gi",
);

/** ` — Ledger ids: `a` · `b` · `c` ` (legacy English) */
const EN_TRIPLE_LEDGER = new RegExp(
  `—\\s*Ledger\\s*ids\\s*:\\s*\`(${UUID})\`\\s*·\\s*\`(${UUID})\`\\s*·\\s*\`(${UUID})\``,
  "gi",
);

/** (`uuid` / `uuid` / `uuid`) with backticks (legacy footnote tail). */
const PAREN_BACKTICK_TRIPLE = new RegExp(
  `\\(\\s*\`(${UUID})\`\\s*\\/\\s*\`(${UUID})\`\\s*\\/\\s*\`(${UUID})\`\\s*\\)`,
  "g",
);

/** (uuid / uuid / uuid) without backticks (legacy). */
const PAREN_PLAIN_TRIPLE = new RegExp(
  `\\(\\s*(${UUID})\\s*\\/\\s*(${UUID})\\s*\\/\\s*(${UUID})\\s*\\)`,
  "g",
);

function traceKeySuffix(middleId: string, isEnglish: boolean): string {
  return isEnglish
    ? ` — Trace key: \`${middleId}\``
    : ` — 追溯键: \`${middleId}\``;
}

function stripEmptySpans(md: string): string {
  let prev = "";
  let out = md;
  while (out !== prev) {
    prev = out;
    out = out.replace(EMPTY_SPAN, "");
  }
  return out;
}

function detectEnglishFootnoteSection(markdown: string): boolean {
  const hasZh =
    markdown.includes("## 证据来源") || markdown.includes("## 证据脚注");
  return markdown.includes("## Evidence footnotes") && !hasZh;
}

/**
 * Returns cleaned Markdown safe for ReactMarkdown, clipboard, and .md download.
 */
export function sanitizeReportMarkdown(markdown: string): string {
  const isEnglish = detectEnglishFootnoteSection(markdown);

  let md = markdown;
  md = md.replace(LEGACY_EVIDENCE_SPAN, "");
  md = md.replace(URL_WRAPPED_SPAN, "$1");
  md = stripEmptySpans(md);

  md = md.replace(ZH_TRIPLE_LEDGER, (_m, _u1: string, u2: string) => traceKeySuffix(u2, false));
  md = md.replace(EN_TRIPLE_LEDGER, (_m, _u1: string, u2: string) => traceKeySuffix(u2, true));

  md = md.replace(PAREN_BACKTICK_TRIPLE, (_m, _u1: string, u2: string) => traceKeySuffix(u2, isEnglish));
  md = md.replace(PAREN_PLAIN_TRIPLE, (_m, _u1: string, u2: string) => traceKeySuffix(u2, isEnglish));

  md = stripEmptySpans(md);

  md = md.replace(/^##\s*证据脚注\s*$/gm, "## 证据来源");
  md = md.replace(/—\s*追溯键（claim_evidence）[：:]/g, "— 追溯键:");
  md = md.replace(/—\s*Trace key \(claim_evidence\):\s*/gi, "— Trace key: ");

  return md;
}
