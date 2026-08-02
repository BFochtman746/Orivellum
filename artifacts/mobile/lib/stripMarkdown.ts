/**
 * stripMarkdown — convert a Markdown string to plain text suitable for
 * list-preview snippets (conversation preview, notification bodies, etc.).
 *
 * Handles: bold/italic/strikethrough (both * and _), inline + fenced code,
 * ATX headings, blockquotes, list bullets, ordered-list numbers, images,
 * links (keep label), autolinks, HTML tags, and leading/trailing whitespace.
 * Collapses interior newlines to a single space so the result fits one line.
 */
export function stripMarkdown(text: string): string {
  return (
    text
      // Fenced code blocks (``` or ~~~)
      .replace(/^```[\s\S]*?^```\s*$/gm, '')
      .replace(/^~~~[\s\S]*?^~~~\s*$/gm, '')
      // Inline code
      .replace(/`{1,3}[^`]*`{1,3}/g, '')
      // ATX headings
      .replace(/^#{1,6}\s+/gm, '')
      // Bold+italic (***…*** or ___…___)
      .replace(/\*{3}(.+?)\*{3}/gs, '$1')
      .replace(/_{3}(.+?)_{3}/gs, '$1')
      // Bold (**…** or __…__)
      .replace(/\*{2}(.+?)\*{2}/gs, '$1')
      .replace(/_{2}(.+?)_{2}/gs, '$1')
      // Italic (*…* or _…_) — require non-space adj chars to avoid false positives
      .replace(/\*([^\s*][^*]*[^\s*]|\S)\*/gs, '$1')
      .replace(/_([^\s_][^_]*[^\s_]|\S)_/gs, '$1')
      // Strikethrough (~~…~~)
      .replace(/~~(.+?)~~/gs, '$1')
      // Images → remove entirely
      .replace(/!\[.*?\]\(.*?\)/g, '')
      // Links → keep label
      .replace(/\[(.+?)\]\(.*?\)/g, '$1')
      // Autolinks <url> or <email>
      .replace(/<https?:\/\/[^>]+>/g, '')
      .replace(/<[a-zA-Z0-9._%+-]+@[^>]+>/g, '')
      // HTML tags
      .replace(/<\/?[a-z][a-z0-9]*(?:\s[^>]*)?\/?>/gi, '')
      // Blockquotes
      .replace(/^>\s?/gm, '')
      // Unordered list bullets
      .replace(/^\s*[-*+]\s+/gm, '')
      // Ordered list numbers
      .replace(/^\s*\d+\.\s+/gm, '')
      // Horizontal rules
      .replace(/^[-*_]{3,}\s*$/gm, '')
      // Collapse newlines/whitespace to single space
      .replace(/\s+/g, ' ')
      .trim()
  );
}
