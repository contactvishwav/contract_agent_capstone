import React, { FormEvent, KeyboardEvent, useRef } from "react";
import { Textarea } from "../../shared/ui/textarea";
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectTrigger,
    SelectValue
} from "../../shared/ui/select";
import { Button } from "../../shared/ui/button";
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { MouseEvent } from 'react';
import { SendHorizontal } from "lucide-react";
import { Message, MessagePart, useChat } from "./provider";
import { authHeader, clearSession } from "../../../lib/authStore";
import { useContractHistory } from "../../../contexts/ContractHistoryContext";
import { useChatSession } from "../../../contexts/ChatSessionContext";

// Real, confirmed bug this closes: Contract Chat had no way to know
// which contract a question like "Analyze this contract" referred to -
// nothing anywhere in the UI let a user pick one, and the /api/run/
// request had no field for it at all. ALL_CONTRACTS_VALUE is a real
// selectable option (not just "unset"), since tenant-wide search across
// every uploaded contract is a legitimate, common thing to want too -
// this isn't forcing a single-contract scope, it's adding the option.
const ALL_CONTRACTS_VALUE = "__all_contracts__";

export function ChatInput() {
    const [submiting, setSubmiting] = React.useState(false);
    const [promptValue, setPromptValue] = React.useState('');
    const [pendingAutoSubmitId, setPendingAutoSubmitId] = React.useState<number | null>(null);
    const {
        addMessage,
        addMessagePart,
        updateMessageGenerating,
        reset,
        promptRequest,
        consumePromptRequest,
    } = useChat();
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const formRef = useRef<HTMLFormElement>(null);
    const requestController = useRef<AbortController | null>(null);
    const { contracts, selectedContractId, setSelectedContract } = useContractHistory();
    const { activeSession, createSession } = useChatSession();

    // Whenever a session is active, its own contract_id is authoritative
    // (including an explicit null for an All-Contracts session) - it must
    // not be overridden by ContractHistoryContext's "most recently
    // uploaded contract" fallback, or reopening an explicit All-Contracts
    // session would silently snap the dropdown back to some other
    // contract. That fallback only applies in the true blank-slate case,
    // before any session has been created or selected yet.
    const effectiveContractId = activeSession
        ? (activeSession.contract_id ?? ALL_CONTRACTS_VALUE)
        : (selectedContractId || contracts[0]?.contract_id || ALL_CONTRACTS_VALUE);

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (submiting) return;
        const formData = new FormData(event.currentTarget);
        const model = formData.get("model") as string;
        const prompt = formData.get("prompt") as string;
        const contractIdField = formData.get("contract_id") as string;
        const selectedScope = contractIdField && contractIdField !== ALL_CONTRACTS_VALUE ? contractIdField : null;
        const contract_id = activeSession ? activeSession.contract_id : selectedScope;

        if (!prompt.trim()) {
            return;
        }

        setSubmiting(true);

        // Lazy session creation: "New chat" doesn't POST until the first
        // real message actually sends, so an unused click never leaves an
        // empty thread in the switcher.
        let session = activeSession;
        try {
            if (!session) {
                session = await createSession(contract_id, prompt.trim().slice(0, 72));
            }
        } catch {
            setSubmiting(false);
            return;
        }

        const userMessage: Message = {
            id: Date.now().toString(),
            type: "user",
            parts: [{ content: prompt, type: "user_message" }],
            generating: false
        };

        const aiMessage: Message = {
            id: Date.now().toString() + "ai",
            type: "ai",
            parts: [],
            generating: true
        };

        addMessage(userMessage);
        addMessage(aiMessage);
        // removed console log

        // Clear the form after submission
        setPromptValue('');

        const auth = authHeader();
        requestController.current?.abort();
        const controller = new AbortController();
        requestController.current = controller;
        await fetchEventSource('/api/run/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(auth ? { Authorization: auth } : {}),
            },
            body: JSON.stringify({ model, prompt, contract_id, session_id: session.session_id }),
            signal: controller.signal,
            onmessage(event) {
                const data: MessagePart = JSON.parse(event.data);

                if (data.type === "end") {
                    updateMessageGenerating(aiMessage.id, false);
                } else if (data.type === "history") {
                    // No longer needed client-side - the backend persists
                    // every turn to the session itself (backend/main.py's
                    // runner()) once session_id is present, so there is
                    // nothing left for the client to round-trip.
                } else {
                    addMessagePart(aiMessage.id, data);
                }
            },
            async onopen(response) {
                if (response.status === 401) {
                    // Session expired/invalid - matches apiClient.ts's handling
                    // for regular fetch calls, so the login gate takes over
                    // instead of leaving the chat silently stuck.
                    clearSession();
                    throw new Error('Session expired - please sign in again');
                }
            },
            onclose() {
                setSubmiting(false);
                requestController.current = null;
            },
            onerror() {
                setSubmiting(false);
                requestController.current = null;
                // removed console error
                addMessagePart(aiMessage.id, { type: "ai_message", content: "Error: Failed to generate the response." });
                updateMessageGenerating(aiMessage.id, false);
                throw new Error('Connection closed due to error');
            }
        });
    };

    const handleClear = (event: MouseEvent) => {
        event.preventDefault();
        requestController.current?.abort();
        requestController.current = null;
        setSubmiting(false);
        reset();
        setPromptValue('');
    };

    const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            const form = event.currentTarget.form;
            if (form) {
                form.requestSubmit();
            }
        }
    };

    // Controlled (not defaultValue, unlike the model select below): needs
    // to react to a contract being selected/uploaded elsewhere in the app
    // (e.g. on the Intelligence page), not just the user's own choice here.
    const [contractSelection, setContractSelection] = React.useState(effectiveContractId);
    React.useEffect(() => {
        setContractSelection(effectiveContractId);
    }, [effectiveContractId]);

    React.useEffect(() => {
        if (!promptRequest || submiting) return;
        setPromptValue(promptRequest.prompt);
        setPendingAutoSubmitId(promptRequest.id);
    }, [promptRequest, submiting]);

    React.useEffect(() => {
        if (
            pendingAutoSubmitId === null ||
            promptRequest?.id !== pendingAutoSubmitId ||
            promptValue !== promptRequest.prompt ||
            submiting
        ) return;
        consumePromptRequest(pendingAutoSubmitId);
        setPendingAutoSubmitId(null);
        formRef.current?.requestSubmit();
    }, [consumePromptRequest, pendingAutoSubmitId, promptRequest, promptValue, submiting]);

    React.useEffect(() => () => requestController.current?.abort(), []);

    const handleContractChange = (value: string) => {
        setContractSelection(value);
        setSelectedContract(value === ALL_CONTRACTS_VALUE ? null : value);
    };

    return (
        <div className="flex-0">
            <form ref={formRef} className="flex flex-col gap-2 relative" onSubmit={handleSubmit}>
                <Textarea
                    name="prompt"
                    className="m-0 max-h-[400px]"
                    placeholder="Type your prompt here!"
                    onKeyDown={handleKeyDown}
                    ref={textareaRef}
                    value={promptValue}
                    onChange={(event) => setPromptValue(event.target.value)}
                />
                <div className="flex gap-2">
                    <div className="flex flex-1 flex-col gap-1">
                        <span className="text-xs font-medium text-muted-foreground">Contract scope</span>
                        <Select
                            name="contract_id"
                            value={contractSelection}
                            onValueChange={handleContractChange}
                            disabled={Boolean(activeSession)}
                        >
                            <SelectTrigger className="text-foreground" aria-label="Contract scope">
                                <SelectValue placeholder="Which contract?" />
                            </SelectTrigger>
                            <SelectContent position="popper" sideOffset={4} collisionPadding={12} className="max-h-[8rem]">
                                <SelectGroup>
                                    <SelectItem value={ALL_CONTRACTS_VALUE}>All contracts</SelectItem>
                                    {contracts.map((c) => (
                                        <SelectItem key={c.contract_id} value={c.contract_id}>
                                            {c.filename}
                                        </SelectItem>
                                    ))}
                                </SelectGroup>
                            </SelectContent>
                        </Select>
                        {activeSession && (
                            <span className="text-xs text-muted-foreground">Scope is locked for this conversation. Start a new chat to change it.</span>
                        )}
                    </div>
                    <Select name="model" defaultValue="gemini-2.5-flash">
                        <SelectTrigger className=" flex-1 text-foreground" aria-label="Model">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectGroup>
                                <SelectItem value="gemini-2.5-flash">gemini-2.5-flash</SelectItem>
                                <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                            </SelectGroup>
                        </SelectContent>
                    </Select>
                    <Button variant="outline" className="flex-0" onClick={handleClear}>
                        Reset
                    </Button>
                    <Button className="flex-0" type="submit" disabled={submiting}>
                        Send your prompt now!
                        <SendHorizontal />
                    </Button>
                </div>
            </form>
        </div>
    );
}
