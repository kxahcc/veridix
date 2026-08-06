import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownView({
  value,
  className,
  testId,
}: {
  value: string;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      className={`markdown-view${className ? ` ${className}` : ""}`}
      data-testid={testId ?? "markdown-view"}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {value || ""}
      </ReactMarkdown>
    </div>
  );
}
