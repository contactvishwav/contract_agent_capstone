import { Fragment, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { Loader } from "../../shared/ui/loader";
import { ChatCitation } from "../../../services/chatSessionApi";
import { Message, MessagePart } from "./provider";

interface Props {
    message: Message;
}

type RenderGroup = MessagePart | { type: "ai_markdown"; content: string };

function groupParts(parts: MessagePart[]): RenderGroup[] {
    const grouped: RenderGroup[] = [];
    for (const part of parts) {
        if (part.type === "ai_message") {
            const previous = grouped[grouped.length - 1];
            if (previous?.type === "ai_markdown") {
                previous.content += part.content;
            } else {
                grouped.push({ type: "ai_markdown", content: part.content });
            }
        } else {
            grouped.push(part);
        }
    }
    return grouped;
}

function parseCitations(content: string): ChatCitation[] {
    try {
        const value = JSON.parse(content);
        return Array.isArray(value)
            ? value.filter((citation) => citation && typeof citation.citation_id === "string" && citation.validation_status === "tenant_active")
            : [];
    } catch {
        return [];
    }
}

function CitationPanel({ content }: { content: string }) {
    const citations = parseCitations(content);
    if (!citations.length) return null;
    return (
        <aside className="mt-3 rounded-md border bg-muted/40 p-3" aria-label="Sources">
            <div className="text-xs font-semibold uppercase tracking-wide">Sources</div>
            <ol className="mt-2 space-y-2 text-sm">
                {citations.map((citation) => (
                    <li key={citation.citation_id} className="rounded border bg-background p-2">
                        <div className="font-medium">
                            {citation.filename} · {citation.source_type}
                            {citation.page != null ? ` · page ${citation.page}` : ""}
                        </div>
                        {citation.excerpt && <p className="mt-1 text-muted-foreground">{citation.excerpt}</p>}
                        <div className="mt-1 text-xs text-muted-foreground">
                            {[citation.section_title, citation.clause_type, citation.chunk_index != null ? `chunk ${citation.chunk_index}` : null]
                                .filter(Boolean).join(" · ")}
                        </div>
                    </li>
                ))}
            </ol>
        </aside>
    );
}

function renderPart(part: RenderGroup, index: number): ReactNode {
    switch (part.type) {
        case "ai_markdown":
            return (
                <div key={index} className="space-y-2 break-words">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        rehypePlugins={[rehypeSanitize]}
                        components={{
                            a: ({ children, ...props }) => <a {...props} className="underline" rel="noreferrer">{children}</a>,
                            ul: ({ children }) => <ul className="ml-5 list-disc">{children}</ul>,
                            ol: ({ children }) => <ol className="ml-5 list-decimal">{children}</ol>,
                            table: ({ children }) => <div className="overflow-x-auto"><table className="border-collapse border">{children}</table></div>,
                            th: ({ children }) => <th className="border p-1 text-left">{children}</th>,
                            td: ({ children }) => <td className="border p-1 align-top">{children}</td>,
                            code: ({ children }) => <code className="rounded bg-muted px-1 font-mono text-sm">{children}</code>,
                        }}
                    >
                        {part.content}
                    </ReactMarkdown>
                </div>
            );
        case "tool_call":
            return <details key={index} className="my-3 cursor-pointer">
                <summary>Tool call</summary>
                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm whitespace-pre-wrap">{part.content}</code>
            </details>;
        case "tool_message":
            return <details key={index} className="my-3 cursor-pointer">
                <summary>Tool message</summary>
                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm whitespace-pre-wrap">{part.content}</code>
            </details>;
        case "citations":
            return <CitationPanel key={index} content={part.content} />;
        case "error":
            return <div key={index} role="alert" className="text-red-700">{part.content}</div>;
        default:
            return <Fragment key={index}>{part.content}</Fragment>;
    }
}

export function ChatMessage({ message }: Props) {
    const { type, parts, generating } = message;
    return (
        <div className={`py-3 gap-0 ${type === "ai" ? "opacity-100" : "opacity-60"}`}>
            <strong className="text-xs">{type === "ai" ? "AI" : "USER"}</strong>
            <div>
                {groupParts(parts).map(renderPart)}
                {generating && <Loader className="inline-flex" />}
            </div>
        </div>
    );
}
