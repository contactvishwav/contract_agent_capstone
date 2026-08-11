import { Fragment, ReactNode, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { Loader } from "../../shared/ui/loader";
import { ChatAttachmentRef, ChatCitation } from "../../../services/chatSessionApi";
import { Message, MessagePart } from "./provider";
import { PdfCitationViewer } from "./PdfCitationViewer";
import { AttachmentImage } from "./AttachmentImage";
import { useChatSession } from "../../../contexts/ChatSessionContext";

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
    const [openCitation, setOpenCitation] = useState<ChatCitation | null>(null);
    if (!citations.length) return null;
    return (
        <aside className="mt-3" aria-label="Sources">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold uppercase tracking-wide">Sources · {citations.length}</span>
                {citations.map((citation) => {
                    const bestLocator = citation.page != null
                        ? `p. ${citation.page}`
                        : citation.section_title
                            ? `§ ${citation.section_title}`
                            : citation.clause_type
                                ? `§ ${citation.clause_type}`
                                : "excerpt";
                    const canOpen = Boolean(
                        citation.source_available &&
                        citation.page != null &&
                        (citation.provenance_status === "exact" || citation.provenance_status === "page_only")
                    );
                    const label = `${citation.filename} · ${bestLocator}`;
                    return canOpen ? (
                        <button
                            key={citation.citation_id}
                            type="button"
                            className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 font-medium text-blue-800 hover:bg-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                            onClick={() => setOpenCitation(citation)}
                            aria-label={`Open source ${label}`}
                            title={citation.excerpt || label}
                        >
                            {label}
                        </button>
                    ) : (
                        <span
                            key={citation.citation_id}
                            className="rounded-full border bg-muted px-2.5 py-1 text-muted-foreground"
                            title={citation.excerpt || "No verified page locator is available"}
                            aria-label={`${label}; source preview only`}
                        >
                            {label}
                        </span>
                    );
                })}
            </div>
            <details className="mt-2 text-xs text-muted-foreground">
                <summary className="cursor-pointer select-none">Source excerpts</summary>
                <ol className="mt-2 space-y-2 border-l pl-3">
                    {citations.map((citation) => (
                        <li key={citation.citation_id}>
                            <span className="font-medium text-foreground">{citation.filename}</span>
                            {citation.excerpt ? ` — ${citation.excerpt}` : " — Excerpt unavailable"}
                        </li>
                    ))}
                </ol>
            </details>
            {openCitation && <PdfCitationViewer citation={openCitation} onClose={() => setOpenCitation(null)} />}
        </aside>
    );
}

function parseAttachments(content: string): ChatAttachmentRef[] {
    try {
        const value = JSON.parse(content);
        return Array.isArray(value)
            ? value.filter((item) => item && typeof item.attachment_id === "string")
            : [];
    } catch {
        return [];
    }
}

function AttachmentsRow({ content, sessionId }: { content: string; sessionId: string | null }) {
    const attachments = parseAttachments(content);
    if (!attachments.length || !sessionId) return null;
    return (
        <div className="mb-2 flex flex-wrap gap-2" aria-label="Attached images">
            {attachments.map((attachment) => (
                <AttachmentImage key={attachment.attachment_id} sessionId={sessionId} attachmentId={attachment.attachment_id} />
            ))}
        </div>
    );
}

function renderPart(part: RenderGroup, index: number, sessionId: string | null): ReactNode {
    switch (part.type) {
        case "attachments":
            return <AttachmentsRow key={index} content={part.content} sessionId={sessionId} />;
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
    const { type, parts, generating, verifying } = message;
    const { activeSession } = useChatSession();
    const hasAnswer = parts.some((part) => part.type === "ai_message");
    const hasCitations = parts.some((part) => part.type === "citations" && parseCitations(part.content).length > 0);
    const attribution = [...parts].reverse().find(
        (part) => part.type === "ai_message" && (part.actual_model || part.actual_provider),
    );
    const sessionId = activeSession?.session_id ?? null;
    return (
        <div className={`py-3 gap-0 ${type === "ai" ? "opacity-100" : "opacity-60"}`}>
            <strong className="text-xs">{type === "ai" ? "AI" : "USER"}</strong>
            <div>
                {groupParts(parts).map((part, index) => renderPart(part, index, sessionId))}
                {type === "ai" && attribution?.actual_model && (
                    <p className="mt-2 text-xs text-muted-foreground">
                        Actual model: {attribution.actual_provider ? `${attribution.actual_provider} · ` : ""}{attribution.actual_model}
                        {attribution.fallback_occurred ? ` · fallback${attribution.fallback_reason ? ` (${attribution.fallback_reason})` : ""}` : ""}
                    </p>
                )}
                {type === "ai" && hasAnswer && !generating && !hasCitations && (
                    <p className="mt-2 text-xs text-muted-foreground">No verified source citations were produced for this answer.</p>
                )}
                {generating && (
                    verifying ? (
                        <p className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                            <Loader className="inline-flex" /> Verifying response…
                        </p>
                    ) : (
                        <Loader className="inline-flex" />
                    )
                )}
            </div>
        </div>
    );
}
