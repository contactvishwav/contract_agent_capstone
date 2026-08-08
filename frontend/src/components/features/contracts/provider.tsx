import React, { createContext, useContext, useState, useCallback } from "react";

export type MessagePartType = "user_message" | "ai_message" | "tool_call" | "tool_message" | "history" | "end";

export type MessagePart = {
    type: MessagePartType;
    content: string;
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
};

const ChatProviderContext = createContext<ChatProviderState>(initialState);

export function ChatProvider({ children }: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [promptRequest, setPromptRequest] = useState<{ id: number; prompt: string } | null>(null);
    const promptSequence = React.useRef(0);

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
