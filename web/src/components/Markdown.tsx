import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// The answer agent often replies in Markdown (bold, lists, tables, links, code).
// We render it through react-markdown — which, with no rehype-raw plugin, never
// renders raw HTML, so untrusted LLM output can't inject markup. Every element
// is mapped to the app's dark pitch/gold theme so the answer keeps the same look
// whether it's plain prose or rich Markdown. Size/colour come from the wrapper's
// className (see AnswerCard), so the same map works for the big answer and the
// smaller abstention note.
const components: Components = {
  p: ({ children }) => <p className="mb-3 leading-relaxed last:mb-0">{children}</p>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-gold-200 underline decoration-gold-500/40 underline-offset-2 transition hover:text-gold-100 hover:decoration-gold-300"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold text-pitch-50">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-pitch-400 line-through">{children}</del>,
  ul: ({ children }) => (
    <ul className="mb-3 list-disc space-y-1 pl-5 marker:text-pitch-500 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 list-decimal space-y-1 pl-5 marker:text-pitch-500 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => (
    <h1 className="mb-2 mt-1 text-xl font-semibold text-pitch-50 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-1 text-lg font-semibold text-pitch-50 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-1 text-base font-semibold text-pitch-100 first:mt-0">{children}</h3>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-3 border-l-2 border-gold-500/40 pl-3 italic text-pitch-200 last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-pitch-700/50" />,
  // react-markdown (v9+) dropped the `inline` flag; a fenced block carries a
  // `language-*` class, inline code does not. Block code stays unstyled here and
  // inherits the <pre> container's background; inline code gets its own chip.
  code: ({ className, children, ...props }) => {
    const isBlock = /language-/.test(className ?? "");
    if (isBlock) {
      return (
        <code className={`${className ?? ""} font-mono text-[0.85em]`} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-pitch-800/70 px-1.5 py-0.5 font-mono text-[0.85em] text-gold-100">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mb-3 overflow-x-auto rounded-lg border border-pitch-800/60 bg-pitch-950/70 p-3 text-sm leading-relaxed text-pitch-100 last:mb-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="mb-3 overflow-x-auto last:mb-0">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-pitch-700/50 bg-pitch-800/50 px-3 py-1.5 text-left font-semibold text-pitch-100">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-pitch-800/60 px-3 py-1.5 text-pitch-100">{children}</td>
  ),
};

export function Markdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
