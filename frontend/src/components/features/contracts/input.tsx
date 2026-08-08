import React, { KeyboardEvent, useRef } from "react";
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

// Real, confirmed bug this closes: Contract Chat had no way to know
// which contract a question like "Analyze this contract" referred to -
// nothing anywhere in the UI let a user pick one, and the /api/run/
// request had no field for it at all. ALL_CONTRACTS_VALUE is a real
// selectable option (not just "unset"), since tenant-wide search across
// every uploaded contract is a legitimate, common thing to want too -
// this isn't forcing a single-contract scope, it's adding the option.
const ALL_CONTRACTS_VALUE = "__all_contracts__";

export function ChatInput() {
    const history = useRef<string[]>([])
    const [submiting, setSubmiting] = React.useState(false);
    const { addMessage, addMessagePart, updateMessageGenerating, reset } = useChat();
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const { contracts, selectedContractId, setSelectedContract } = useContractHistory();

    // Defaults to whatever contract is already selected elsewhere in the
    // app (e.g. just uploaded or opened on the Intelligence page) - "tied
    // to whatever contract they're viewing", not just an empty dropdown -
    // falling back to the most recently uploaded contract if nothing is
    // selected yet, and to "all contracts" only if there's no history at all.
    const effectiveContractId = selectedContractId || contracts[0]?.contract_id || ALL_CONTRACTS_VALUE;

    const handleSubmit = async (event: any) => {
        event.preventDefault();
        const formData = new FormData(event.target);
        const model = formData.get("model") as string;
        const prompt = formData.get("prompt") as string;
        const contractIdField = formData.get("contract_id") as string;
        const contract_id = contractIdField && contractIdField !== ALL_CONTRACTS_VALUE ? contractIdField : null;

        if (!prompt.trim()) {
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
        event.target.reset();

        const auth = authHeader();
        await fetchEventSource('/api/run/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(auth ? { Authorization: auth } : {}),
            },
            body: JSON.stringify({ model, prompt, history: JSON.stringify(history.current), contract_id }),
            onmessage(event) {
                const data: MessagePart = JSON.parse(event.data);

                if (data.type === "end") {
                    updateMessageGenerating(aiMessage.id, false);
                } else if (data.type === "history") {
                    history.current = [...history.current, ...data.content]
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
                setSubmiting(true);
            },
            onclose() {
                setSubmiting(false);
            },
            onerror() {
                setSubmiting(false);
                // removed console error
                addMessagePart(aiMessage.id, { type: "ai_message", content: "Error: Failed to generate the response." });
                updateMessageGenerating(aiMessage.id, false);
                throw new Error('Connection closed due to error');
            }
        });
    };

    const handleClear = (event: MouseEvent) => {
        event.preventDefault();
        reset();
        history.current = [];
        if (textareaRef.current) {
            textareaRef.current.value = "";
        }
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

    const handleContractChange = (value: string) => {
        setContractSelection(value);
        setSelectedContract(value === ALL_CONTRACTS_VALUE ? null : value);
    };

    return (
        <div className="flex-0">
            <form className="flex flex-col gap-2 relative" onSubmit={handleSubmit}>
                <Textarea
                    name="prompt"
                    className="m-0 max-h-[400px]"
                    placeholder="Type your prompt here!"
                    onKeyDown={handleKeyDown}
                    ref={textareaRef}
                />
                <div className="flex gap-2">
                    <Select name="contract_id" value={contractSelection} onValueChange={handleContractChange}>
                        <SelectTrigger className="flex-1 text-foreground">
                            <SelectValue placeholder="Which contract?" />
                        </SelectTrigger>
                        <SelectContent>
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
                    <Select name="model" defaultValue="gemini-2.5-flash">
                        <SelectTrigger className=" flex-1 text-foreground">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectGroup>
                                <SelectItem value="gemini-1.5-pro">gemini-1.5-pro</SelectItem>
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