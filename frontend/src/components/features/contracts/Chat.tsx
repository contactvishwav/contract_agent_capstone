import { ChatProvider } from "./provider";
import { ChatInput } from "./input";
import { ChatOutput } from "./output";
import { SessionSwitcher } from "./SessionSwitcher";

export function Chat() {
    return (
        <ChatProvider>
            <div className="flex h-full gap-4">
                <SessionSwitcher />
                <div className="flex flex-col h-full gap-4 flex-1 min-w-0">
                    <ChatOutput />
                    <ChatInput />
                </div>
            </div>
        </ChatProvider>
    );
}