/**
 * Rich markdown renderer — extracted from the chat page (WP5) so that
 * react-markdown + remark-gfm + rehype-highlight (lowlight grammars are the
 * heavy part) live in their own lazily-loaded chunk. Import this component
 * with React.lazy and render plain text as the Suspense fallback; the chunk
 * loads once at first use and is cached by the service worker.
 */
import React, { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/atom-one-dark.css";
import { Check, Copy } from "lucide-react";
import { copyToClipboard } from "@/lib/uuid";

// ─── Code block with copy button ─────────────────────────────────────────────

function CodeBlock({ lang, className, children }: { lang: string; className?: string; children: React.ReactNode }) {
  const codeRef = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    const text = codeRef.current?.textContent ?? "";
    copyToClipboard(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <span className="block my-3 rounded-lg overflow-hidden border border-white/10 shadow-md">
      <span className="flex items-center justify-between px-3 py-1.5 bg-zinc-800 border-b border-white/10">
        <span className="text-[10px] font-mono uppercase tracking-wide text-zinc-400">{lang || " "}</span>
        <button
          type="button"
          onClick={handleCopy}
          title="Copy code"
          className="flex items-center gap-1 text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          {copied ? <Check className="w-3 h-3" style={{ color: "var(--gd-success)" }} /> : <Copy className="w-3 h-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </span>
      <code
        ref={codeRef}
        className={`block bg-zinc-900 text-zinc-100 px-4 py-3 text-xs font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto ${className ?? ""}`}
      >
        {children}
      </code>
    </span>
  );
}

// ─── Markdown renderer ────────────────────────────────────────────────────────

export default function MarkdownContent({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        code: ({ className, children }) => {
          const lang = className?.replace("language-", "").replace(/\s*hljs.*/, "") ?? "";
          const isBlock = className?.startsWith("language-") || className?.startsWith("hljs");
          return isBlock ? (
            <CodeBlock lang={lang} className={className}>
              {children}
            </CodeBlock>
          ) : (
            <code className="bg-zinc-800 text-zinc-200 rounded px-1.5 py-0.5 text-[0.8em] font-mono">
              {children}
            </code>
          );
        },
        pre: ({ children }) => <div className="my-0">{children}</div>,
        ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>,
        li: ({ children }) => <li className="text-sm">{children}</li>,
        h1: ({ children }) => <h1 className="text-base font-semibold mb-1 mt-2">{children}</h1>,
        h2: ({ children }) => <h2 className="text-sm font-semibold mb-1 mt-2">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-medium mb-1 mt-1">{children}</h3>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-border pl-3 italic text-muted-foreground my-2">{children}</blockquote>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:opacity-70">
            {children}
          </a>
        ),
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        hr: () => <hr className="border-border my-3" />,
        // Allow data:image/... base64 URLs (generated images) while keeping
        // the default sanitizer for all other URL types.
        img: ({ src, alt }) => {
          const safe =
            typeof src === "string" &&
            (/^data:image\/(png|jpeg|webp|gif);base64,/.test(src) ||
              /^https?:\/\//.test(src) ||
              src.startsWith("/") ||
              src.startsWith("./"));
          if (!safe) return null;
          return (
            <img
              src={src}
              alt={alt ?? ""}
              className="max-w-full rounded-lg border border-border/40 my-2"
            />
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
