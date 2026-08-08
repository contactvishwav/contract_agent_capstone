import React from "react";
import { Button } from "../../shared/ui/button";
import { Badge } from "../../shared/ui/badge";
import { useChatSession } from "../../../contexts/ChatSessionContext";
import { useContractHistory } from "../../../contexts/ContractHistoryContext";
import { useChat } from "./provider";
import { chatSessionApi, ChatSessionSummary } from "../../../services/chatSessionApi";
import { groupStoredMessagesIntoUiMessages } from "./sessionMessages";

// Flat list, sorted most-recently-updated-first (already the order /api/
// chat/sessions returns), with the contract shown as a badge per row -
// not grouped by contract. Grouping adds real complexity (per-group
// collapse state, an All-Contracts bucket, group-vs-item ordering
// ambiguity) for no real benefit given sessions are human-initiated
// (lazy creation - see ChatSessionContext) and therefore bounded in
// count; a badge already answers "which contract is this about" at a
// glance without it.
export function SessionSwitcher() {
    const { sessions, activeSession, selectSession, startNewSession } = useChatSession();
    const { contracts, setSelectedContract } = useContractHistory();
    const { replaceMessages, reset } = useChat();
    const [loadingId, setLoadingId] = React.useState<string | null>(null);

    const contractLabel = (contractId: string | null) => {
        if (!contractId) {
            return "All contracts";
        }
        return contracts.find((c) => c.contract_id === contractId)?.filename || contractId;
    };

    const handleSelect = async (session: ChatSessionSummary) => {
        if (session.session_id === activeSession?.session_id) {
            return;
        }
        setLoadingId(session.session_id);
        try {
            const detail = await chatSessionApi.getSessionDetail(session.session_id);
            selectSession(session);
            // Keeps the rest of the app (Intelligence page, etc.) in sync
            // with whichever session is now open - trusts detail.contract_id
            // as authoritative, including explicit null for All Contracts.
            setSelectedContract(detail.contract_id);
            replaceMessages(groupStoredMessagesIntoUiMessages(detail.messages));
        } catch (e) {
            // Leave the previous conversation on screen rather than
            // clearing it out from under the user on a transient failure.
        } finally {
            setLoadingId(null);
        }
    };

    const handleNewChat = () => {
        startNewSession();
        reset();
    };

    return (
        <div className="w-64 flex-none flex flex-col gap-2 border-r pr-4 overflow-y-auto">
            <Button variant="outline" onClick={handleNewChat} className="w-full">
                New chat
            </Button>
            <div className="flex flex-col gap-1">
                {sessions.map((session) => (
                    <button
                        key={session.session_id}
                        onClick={() => handleSelect(session)}
                        disabled={loadingId === session.session_id}
                        className={`text-left rounded-md p-2 text-sm hover:bg-muted transition-colors ${
                            session.session_id === activeSession?.session_id ? "bg-muted" : ""
                        }`}
                    >
                        <div className="truncate font-medium">{session.title}</div>
                        <Badge variant="secondary" className="mt-1">
                            {contractLabel(session.contract_id)}
                        </Badge>
                    </button>
                ))}
                {sessions.length === 0 && (
                    <p className="text-xs text-muted-foreground p-2">No conversations yet.</p>
                )}
            </div>
        </div>
    );
}
