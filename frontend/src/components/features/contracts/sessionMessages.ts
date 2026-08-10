import { ChatSessionMessage } from "../../../services/chatSessionApi";
import { Message } from "./provider";

// Groups a flat, sequence-ordered list of stored ChatMessage rows (backend/
// infrastructure/chat_session_repository.py) into the Message[] shape
// ChatProvider already renders. Works with zero translation layer because
// ChatMessage.role was deliberately chosen to match MessagePart.type
// exactly ("user_message" | "ai_message" | "tool_call" | "tool_message") -
// see the chat-session feature's design plan.
export function groupStoredMessagesIntoUiMessages(stored: ChatSessionMessage[]): Message[] {
    const messages: Message[] = [];
    let current: Message | null = null;

    for (const row of stored) {
        if (row.role === "user_message") {
            current = null; // force a new ai Message for whatever follows
            const parts: Message["parts"] = [];
            // Attachments render first (same convention Claude/ChatGPT use:
            // image strip above the text) - a JSON-stringified synthetic
            // part, same bolt-on pattern as "citations" below.
            if (row.attachments?.length) {
                parts.push({ type: "attachments", content: JSON.stringify(row.attachments) });
            }
            parts.push({ type: "user_message", content: row.content });
            messages.push({
                id: row.message_id,
                type: "user",
                parts,
                generating: false,
            });
            continue;
        }

        if (!current) {
            current = { id: row.message_id, type: "ai", parts: [], generating: false };
            messages.push(current);
        }
        current.parts.push({
            type: row.role === "ai_message" && row.terminal_status && row.terminal_status !== "passed"
                ? "error"
                : row.role,
            content: row.content,
            ...(row.terminal_status ? { status: row.terminal_status } : {}),
            reason_category: row.terminal_reason,
            requested_model: row.requested_model,
            actual_model: row.actual_model || row.model,
            requested_provider: row.requested_provider,
            actual_provider: row.actual_provider,
            fallback_occurred: row.fallback_occurred,
            fallback_reason: row.fallback_reason,
            prompt_version: row.prompt_version,
            execution_path: row.execution_path,
        });
        if (row.role === "ai_message" && row.citations?.length) {
            current.parts.push({ type: "citations", content: JSON.stringify(row.citations) });
        }
    }

    return messages;
}
