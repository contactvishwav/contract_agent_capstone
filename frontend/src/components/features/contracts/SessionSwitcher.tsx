import React from "react";
import { Button } from "../../shared/ui/button";
import { Badge } from "../../shared/ui/badge";
import { useChatSession } from "../../../contexts/ChatSessionContext";
import { useContractHistory } from "../../../contexts/ContractHistoryContext";
import { useChat } from "./provider";
import { chatSessionApi, ChatSessionSummary } from "../../../services/chatSessionApi";
import { groupStoredMessagesIntoUiMessages } from "./sessionMessages";
import { Check, Pencil, X } from "lucide-react";

// Flat list, sorted most-recently-updated-first (already the order /api/
// chat/sessions returns), with the contract shown as a badge per row -
// not grouped by contract. Grouping adds real complexity (per-group
// collapse state, an All-Contracts bucket, group-vs-item ordering
// ambiguity) for no real benefit given sessions are human-initiated
// (lazy creation - see ChatSessionContext) and therefore bounded in
// count; a badge already answers "which contract is this about" at a
// glance without it.
export function SessionSwitcher() {
    const {
        sessions,
        activeSession,
        isLoadingSessions,
        sessionListError,
        refreshSessions,
        selectSession,
        startNewSession,
        renameSession,
    } = useChatSession();
    const { contracts, setSelectedContract } = useContractHistory();
    const { replaceMessages, reset, stopActiveRequest } = useChat();
    const [loadingId, setLoadingId] = React.useState<string | null>(null);
    const [loadError, setLoadError] = React.useState<string | null>(null);
    const [editingId, setEditingId] = React.useState<string | null>(null);
    const [titleDraft, setTitleDraft] = React.useState("");
    const [renameError, setRenameError] = React.useState<string | null>(null);
    const loadedSessionId = React.useRef<string | null>(null);

    const contractLabel = (contractId: string | null) => {
        if (!contractId) {
            return "All contracts";
        }
        return contracts.find((c) => c.contract_id === contractId)?.filename || contractId;
    };

    const loadSession = React.useCallback(async (session: ChatSessionSummary) => {
        setLoadingId(session.session_id);
        setLoadError(null);
        try {
            const detail = await chatSessionApi.getSessionDetail(session.session_id);
            selectSession(session);
            // Keeps the rest of the app (Intelligence page, etc.) in sync
            // with whichever session is now open - trusts detail.contract_id
            // as authoritative, including explicit null for All Contracts.
            setSelectedContract(detail.contract_id);
            replaceMessages(groupStoredMessagesIntoUiMessages(detail.messages));
            loadedSessionId.current = session.session_id;
        } catch {
            setLoadError('Could not load this conversation. Try again.');
        } finally {
            setLoadingId(null);
        }
    }, [replaceMessages, selectSession, setSelectedContract]);

    React.useEffect(() => {
        // A just-created session is intentionally empty until ChatInput's
        // first SSE request persists its turn. Fetching that empty detail
        // here races the optimistic user/AI messages and can clear them
        // mid-stream. Restored sessions have a server message_count and do
        // need detail bootstrap; explicit row clicks always reload below.
        if (
            activeSession &&
            activeSession.message_count > 0 &&
            loadedSessionId.current !== activeSession.session_id
        ) {
            loadSession(activeSession);
        }
    }, [activeSession, loadSession]);

    const handleSelect = async (session: ChatSessionSummary) => {
        // Deliberately reload even when this is already active. A refresh
        // restores metadata first; the persisted messages still have to be
        // fetched, and a retry must never be short-circuited as a no-op.
        await stopActiveRequest();
        await loadSession(session);
    };

    const handleNewChat = async () => {
        await stopActiveRequest();
        startNewSession();
        loadedSessionId.current = null;
        setLoadError(null);
        reset();
    };

    const beginRename = (session: ChatSessionSummary) => {
        setEditingId(session.session_id);
        setTitleDraft(session.title);
        setRenameError(null);
    };

    const cancelRename = () => {
        setEditingId(null);
        setTitleDraft("");
        setRenameError(null);
    };

    const saveRename = async (sessionId: string) => {
        const title = titleDraft.trim();
        if (!title) {
            setRenameError("Conversation name cannot be blank.");
            return;
        }
        try {
            await renameSession(sessionId, title);
            cancelRename();
        } catch {
            setRenameError("Could not rename this conversation.");
        }
    };

    return (
        <div className="w-64 flex-none flex flex-col gap-2 border-r pr-4 overflow-y-auto">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Conversations
            </div>
            <Button variant="outline" onClick={() => void handleNewChat()} className="w-full">
                New chat
            </Button>
            {sessionListError && (
                <div className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700" role="alert">
                    {sessionListError}{' '}
                    <button className="underline" onClick={() => refreshSessions()}>Retry</button>
                </div>
            )}
            {loadError && <p className="text-xs text-red-700" role="alert">{loadError}</p>}
            <div className="flex max-h-[13rem] flex-col gap-1 overflow-y-auto pr-1">
                {sessions.map((session) => (
                    <div key={session.session_id} className={`rounded-md p-1 ${session.session_id === activeSession?.session_id ? "bg-muted" : ""}`}>
                        {editingId === session.session_id ? (
                            <div className="flex items-center gap-1">
                                <input
                                    aria-label="Conversation name"
                                    className="min-w-0 flex-1 rounded border bg-background px-2 py-1 text-sm"
                                    value={titleDraft}
                                    maxLength={120}
                                    autoFocus
                                    onChange={(event) => setTitleDraft(event.target.value)}
                                    onKeyDown={(event) => {
                                        event.stopPropagation();
                                        if (event.key === "Enter") { event.preventDefault(); void saveRename(session.session_id); }
                                        if (event.key === "Escape") { event.preventDefault(); cancelRename(); }
                                    }}
                                />
                                <button type="button" aria-label="Save conversation name" onClick={() => void saveRename(session.session_id)}><Check size={16} /></button>
                                <button type="button" aria-label="Cancel rename" onClick={cancelRename}><X size={16} /></button>
                            </div>
                        ) : (
                            <div className="flex items-start gap-1">
                                <button
                                    type="button"
                                    onClick={() => handleSelect(session)}
                                    disabled={loadingId === session.session_id}
                                    className="min-w-0 flex-1 rounded p-1 text-left text-sm hover:bg-muted transition-colors"
                                >
                                    <div className="truncate font-medium">{session.title}</div>
                                    <Badge variant="secondary" className="mt-1">{contractLabel(session.contract_id)}</Badge>
                                </button>
                                <button type="button" aria-label={`Rename ${session.title}`} className="rounded p-1 hover:bg-background" onClick={() => beginRename(session)}>
                                    <Pencil size={14} />
                                </button>
                            </div>
                        )}
                    </div>
                ))}
                {sessions.length === 0 && (
                    <p className="text-xs text-muted-foreground p-2">
                        {isLoadingSessions ? 'Loading conversations…' : 'No conversations yet.'}
                    </p>
                )}
            </div>
            {renameError && <p className="text-xs text-red-700" role="alert">{renameError}</p>}
        </div>
    );
}
