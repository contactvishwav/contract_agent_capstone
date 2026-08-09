import React, { createContext, useContext, useState, useCallback } from "react";

export type MessagePartType = "user_message" | "ai_message" | "tool_call" | "tool_message" | "citations" | "error" | "history" | "end";

export type MessagePart = {
    type: MessagePartType;
    content: string;
    status?: 'passed' | 'rejected' | 'validation_failed' | 'timed_out' | 'cancelled' | 'empty' | 'generation_failed' | 'persistence_failed';
};

export type Message = {
    id: string;
    type: "user" | "ai";
    parts: Array<MessagePart>;
    generating: boolean;
};

type ChatProviderProps = {
    children: React.ReactNode;
};

type ActiveRequestPhase = "running" | "stopping" | "committed";

type ActiveRequest = {
    id: number;
    sessionId: string;
    messageId: string;
    phase: ActiveRequestPhase;
};

type ActiveRequestInternal = ActiveRequest & {
    controller: AbortController;
    cancelServer: () => Promise<boolean>;
    completion: Promise<void>;
    resolveCompletion: () => void;
    stopPromise?: Promise<void>;
};

type ChatProviderState = {
    messages: Message[];
    addMessage: (message: Message) => void;
    addMessagePart: (id: string, part: MessagePart) => void;
    updateMessageGenerating: (id: string, generating: boolean) => void;
    reset: () => void;
    // Loads a persisted chat session's full history in one shot - used
    // when the user opens a prior session from the switcher, distinct
    // from reset() (which clears to empty for a brand new conversation).
    replaceMessages: (messages: Message[]) => void;
    promptRequest: { id: number; prompt: string } | null;
    requestPrompt: (prompt: string) => void;
    consumePromptRequest: (id: number) => void;
    activeRequest: ActiveRequest | null;
    beginRequest: (
        sessionId: string,
        messageId: string,
        cancelServer: () => Promise<boolean>,
    ) => {
        id: number;
        controller: AbortController;
    };
    markRequestCommitted: (id: number) => void;
    finishRequest: (id: number) => void;
    stopActiveRequest: () => Promise<void>;
};

const initialState: ChatProviderState = {
    messages: [],
    addMessage: () => null,
    addMessagePart: () => null,
    updateMessageGenerating: () => null,
    reset: () => null,
    replaceMessages: () => null,
    promptRequest: null,
    requestPrompt: () => null,
    consumePromptRequest: () => null,
    activeRequest: null,
    beginRequest: () => ({ id: 0, controller: new AbortController() }),
    markRequestCommitted: () => null,
    finishRequest: () => null,
    stopActiveRequest: async () => undefined,
};

const ChatProviderContext = createContext<ChatProviderState>(initialState);

export function ChatProvider({ children }: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [promptRequest, setPromptRequest] = useState<{ id: number; prompt: string } | null>(null);
    const promptSequence = React.useRef(0);
    const requestSequence = React.useRef(0);
    const activeRequestRef = React.useRef<ActiveRequestInternal | null>(null);
    const [activeRequest, setActiveRequest] = useState<ActiveRequest | null>(null);

    const addMessage = useCallback((message: Message) => {
        setMessages((prevMessages) => [...prevMessages, message]);
    }, []);

    const addMessagePart = useCallback((messageId: string, part: MessagePart) => {
        setMessages((prevMessages) =>
            prevMessages.map((message) => {
                if (message.id === messageId) {
                    return { ...message, parts: [...message.parts, part] };
                }
                return message;
            })
        );
    }, []);


    const updateMessageGenerating = useCallback((id: string, generating: boolean) => {
        setMessages((prevMessages) =>
            prevMessages.map((message) =>
                message.id === id ? { ...message, generating } : message
            )
        );
    }, []);

    const reset = () => {
        setMessages([]);
    };

    const replaceMessages = useCallback((newMessages: Message[]) => {
        setMessages(newMessages);
    }, []);

    const requestPrompt = useCallback((prompt: string) => {
        promptSequence.current += 1;
        setPromptRequest({ id: promptSequence.current, prompt });
    }, []);

    const consumePromptRequest = useCallback((id: number) => {
        setPromptRequest((current) => current?.id === id ? null : current);
    }, []);

    const beginRequest = useCallback((
        sessionId: string,
        messageId: string,
        cancelServer: () => Promise<boolean>,
    ) => {
        if (activeRequestRef.current) {
            throw new Error("A Contract Chat request is already active");
        }
        requestSequence.current += 1;
        const id = requestSequence.current;
        const controller = new AbortController();
        let resolveCompletion: () => void = () => undefined;
        const completion = new Promise<void>((resolve) => {
            resolveCompletion = resolve;
        });
        const request: ActiveRequestInternal = {
            id,
            sessionId,
            messageId,
            phase: "running",
            controller,
            cancelServer,
            completion,
            resolveCompletion,
        };
        activeRequestRef.current = request;
        setActiveRequest({ id, sessionId, messageId, phase: "running" });
        return { id, controller };
    }, []);

    const markRequestCommitted = useCallback((id: number) => {
        const request = activeRequestRef.current;
        if (!request || request.id !== id || request.phase !== "running") return;
        request.phase = "committed";
        setActiveRequest({
            id: request.id,
            sessionId: request.sessionId,
            messageId: request.messageId,
            phase: "committed",
        });
    }, []);

    const finishRequest = useCallback((id: number) => {
        const request = activeRequestRef.current;
        if (!request || request.id !== id) return;
        activeRequestRef.current = null;
        setActiveRequest(null);
        request.resolveCompletion();
    }, []);

    const stopActiveRequest = useCallback(() => {
        const request = activeRequestRef.current;
        if (!request) return Promise.resolve();
        if (request.stopPromise) return request.stopPromise;
        if (request.phase !== "running") return request.completion;

        request.phase = "stopping";
        setActiveRequest({
            id: request.id,
            sessionId: request.sessionId,
            messageId: request.messageId,
            phase: "stopping",
        });
        request.stopPromise = (async () => {
            try {
                const cancellationWon = await request.cancelServer();
                if (!cancellationWon) {
                    // A durably persisted terminal answer won the race. Leave
                    // the SSE connection attached so its final event remains
                    // the one visible/restorable outcome.
                    if (activeRequestRef.current === request) {
                        request.phase = "committed";
                        setActiveRequest({
                            id: request.id,
                            sessionId: request.sessionId,
                            messageId: request.messageId,
                            phase: "committed",
                        });
                    }
                    await request.completion;
                    return;
                }

                setMessages((previous) => previous.map((message) => {
                    if (message.id !== request.messageId) return message;
                    const alreadyCancelled = message.parts.some(
                        (part) => part.status === "cancelled"
                    );
                    return {
                        ...message,
                        generating: false,
                        parts: alreadyCancelled
                            ? message.parts
                            : [...message.parts, {
                                type: "error" as const,
                                content: "Generation stopped",
                                status: "cancelled" as const,
                            }],
                    };
                }));
                request.controller.abort("server_cancelled");
                await request.completion;
            } catch {
                // Do not claim cancellation when the server did not confirm
                // durable cancellation. Restore the actionable Stop control.
                if (activeRequestRef.current === request) {
                    request.phase = "running";
                    request.stopPromise = undefined;
                    setActiveRequest({
                        id: request.id,
                        sessionId: request.sessionId,
                        messageId: request.messageId,
                        phase: "running",
                    });
                }
            }
        })();
        return request.stopPromise;
    }, []);

    React.useEffect(() => () => {
        // Logout/tenant switch tears down this provider. Preserve the same
        // server-acknowledged cancellation policy used by the visible Stop
        // control instead of merely detaching the browser stream.
        void stopActiveRequest();
    }, [stopActiveRequest]);

    const value = {
        messages,
        addMessage,
        addMessagePart,
        updateMessageGenerating,
        reset,
        replaceMessages,
        promptRequest,
        requestPrompt,
        consumePromptRequest,
        activeRequest,
        beginRequest,
        markRequestCommitted,
        finishRequest,
        stopActiveRequest,
    };

    return (
        <ChatProviderContext.Provider value={value}>
            {children}
        </ChatProviderContext.Provider>
    );
}

export const useChat = () => {
    const context = useContext(ChatProviderContext);

    if (context === undefined) {
        throw new Error("useChat must be used within a ChatProvider");
    }

    return context;
};
