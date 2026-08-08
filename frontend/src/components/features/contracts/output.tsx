import { useEffect, useRef } from "react";
import { useChat } from "./provider";
import { ChatMessage } from "./message";

export function ChatOutput() {
    const { messages, requestPrompt } = useChat();
    const messagesEndRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
        }
    }, [messages]);

    return (
        <div className="flex-1 relative">
            <div className="absolute top-0 left-0 right-0 bottom-0 overflow-y-auto pr-3 inset-shadow-md">
                {messages.length === 0 ? (
                    <div className="flex items-center justify-center h-full">
                        <div className="text-center max-w-2xl mx-auto p-6">
                            <h3 className="text-lg font-semibold text-slate-700 mb-3">Contract Search & Analysis</h3>
                            <p className="text-slate-600 mb-4">Search and analyze contracts from the dataset using natural language queries.</p>
                            <div className="text-left bg-slate-50 rounded-lg p-4">
                                <p className="text-sm font-medium text-slate-700 mb-2">Try these sample queries:</p>
                                <ul className="text-sm text-slate-600 space-y-1">
                                    {[
                                        'how many SOW contracts?',
                                        'how many total active contracts?',
                                        'List all the contract types',
                                        'Display the summary of all the contracts that have monetary value of 50k',
                                        'who are the parties of SOW contracts?',
                                        'Show me all relationships between parties in SOW contracts - who works with whom?',
                                    ].map((query) => (
                                        <li key={query}>
                                            <button
                                                type="button"
                                                className="w-full rounded px-2 py-1 text-left hover:bg-slate-100 hover:text-slate-900"
                                                onClick={() => requestPrompt(query)}
                                            >
                                                {query}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    </div>
                ) : (
                    <div>
                        {messages.map((message) => {
                            return (
                                <ChatMessage
                                    key={message.id}
                                    message={message}
                                />
                            )
                        })}
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>
        </div>
    );
}
