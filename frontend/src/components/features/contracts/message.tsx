import { Fragment } from "react";
import { Loader } from "../../shared/ui/loader";
import { Message } from "./provider";

interface Props {
    message: Message;
}

export function ChatMessage({ message }: Props) {
    const { type, parts, generating, } = message;
    return (
        <div className={`py-3 gap-0 ${type === "ai" ? "opacity-100" : "opacity-60"}`}>
            <strong className="text-xs">{type === "ai" ? "AI" : "USER"}</strong>
            <div>
                {parts.map(({ content, type }, index) => {
                    switch (type) {
                        case "tool_call":
                            return <details key={index} className="my-3 cursor-pointer">
                                <summary>Tool call</summary>
                                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm">{content}</code>
                            </details>
                        case "tool_message":
                            return <details key={index} className="my-3 cursor-pointer">
                                <summary>Tool message</summary>
                                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm">{content}</code>
                            </details>
                        default:
                            return <Fragment key={index}>{content}</Fragment>;
                    }
                })}
                {generating && (
                    <Loader className="inline-flex" />
                )}
            </div>
        </div>
    );
}
