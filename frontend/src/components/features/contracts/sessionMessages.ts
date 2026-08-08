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
            messages.push({
                id: row.message_id,
                type: "user",
                parts: [{ type: "user_message", content: row.content }],
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
        });
        if (row.role === "ai_message" && row.citations?.length) {
            current.parts.push({ type: "citations", content: JSON.stringify(row.citations) });
        }
    }

    return messages;
}
