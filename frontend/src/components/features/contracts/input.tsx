import React, { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useRef } from "react";
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
import { AlertCircle, LoaderCircle, Paperclip, SendHorizontal, Square, X } from "lucide-react";
import { Message, MessagePart, useChat } from "./provider";
import { authHeader, clearSession } from "../../../lib/authStore";
import { useContractHistory } from "../../../contexts/ContractHistoryContext";
import { useChatSession } from "../../../contexts/ChatSessionContext";
import { getWorkflowModels, ModelOption } from "../../../services/modelRegistryApi";
import {
    AttachmentUploadError,
    MAX_ATTACHMENTS_PER_MESSAGE,
    chatAttachmentApi,
    validateAttachmentFile,
} from "../../../services/chatAttachmentApi";
import { ChatSessionSummary } from "../../../services/chatSessionApi";

type PendingAttachment = {
    localId: string;
    file: File;
    previewUrl: string;
    status: "uploading" | "ready" | "error";
    attachmentId?: string;
    error?: string;
};

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
    const [models, setModels] = React.useState<ModelOption[]>([]);
    const [selectedModel, setSelectedModel] = React.useState("");
    const [defaultModel, setDefaultModel] = React.useState("");
    const [modelError, setModelError] = React.useState<string | null>(null);
    const [pendingAttachments, setPendingAttachments] = React.useState<PendingAttachment[]>([]);
    const [attachmentsError, setAttachmentsError] = React.useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const {
        addMessage,
        addMessagePart,
        updateMessageGenerating,
        updateMessageVerifying,
        reset,
        promptRequest,
        consumePromptRequest,
        activeRequest,
        beginRequest,
        markRequestCommitted,
        finishRequest,
        stopActiveRequest,
    } = useChat();
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const formRef = useRef<HTMLFormElement>(null);
    const { contracts, selectedContractId, setSelectedContract } = useContractHistory();
    const { activeSession, createSession } = useChatSession();

    React.useEffect(() => {
        getWorkflowModels("chat").then((registry) => {
            setModels(registry.models);
            setDefaultModel(registry.default_model || registry.models[0]?.id || "");
            setModelError(registry.models.length ? null : "No compatible chat model is configured.");
        }).catch(() => {
            setModelError("Available chat models could not be loaded.");
        });
    }, []);

    const effectiveModel = selectedModel || defaultModel;

    // Stage 2's server-side vision-capability gate (model_registry.py's
    // ModelSpec.capabilities) needs a real frontend surface, not a generic
    // 400 after the fact - getWorkflowModels already returns each model's
    // capabilities, so this is a pure client-side derivation, no extra
    // request. Defaults to true before models have loaded, so no spurious
    // warning flashes on first render.
    const selectedModelOption = models.find((model) => model.id === effectiveModel);
    const selectedModelSupportsVision = models.length === 0 || (selectedModelOption?.capabilities?.includes("vision") ?? false);

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

    // Shared between handleSubmit (typing then sending) and
    // handleFilesSelected (attaching before typing anything) - "New chat"
    // must not POST until there's a real reason to (a message OR an
    // attachment), but attaching first still needs a real session_id to
    // upload against (uploads are session-scoped - see ADR-008).
    const ensureSession = async (): Promise<ChatSessionSummary> => {
        if (activeSession) return activeSession;
        const selectedScope = contractSelection !== ALL_CONTRACTS_VALUE ? contractSelection : null;
        return createSession(selectedScope);
    };

    const handleAttachClick = () => {
        fileInputRef.current?.click();
    };

    const handleFilesSelected = async (event: ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(event.target.files || []);
        event.target.value = ""; // allow re-selecting the same file later
        if (!files.length) return;
        setAttachmentsError(null);

        if (pendingAttachments.length + files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
            setAttachmentsError(`You can attach up to ${MAX_ATTACHMENTS_PER_MESSAGE} images per message.`);
            return;
        }

        let session: ChatSessionSummary;
        try {
            session = await ensureSession();
        } catch {
            setAttachmentsError("Could not start a chat session for this attachment.");
            return;
        }

        for (const file of files) {
            const localId = crypto.randomUUID();
            const previewUrl = URL.createObjectURL(file);
            // Mirrors the server's real limits (5MB, PNG/JPEG/WEBP) so the
            // user gets immediate feedback instead of a round-trip failure -
            // the server still re-validates for real (magic bytes, not just
            // the client-declared type), this is UX only, not the security
            // boundary.
            const clientError = validateAttachmentFile(file);

            setPendingAttachments((previous) => [...previous, {
                localId, file, previewUrl,
                status: clientError ? "error" : "uploading",
                error: clientError ?? undefined,
            }]);

            if (clientError) continue;

            try {
                const uploaded = await chatAttachmentApi.upload(session.session_id, file);
                setPendingAttachments((previous) => previous.map((attachment) =>
                    attachment.localId === localId
                        ? { ...attachment, status: "ready" as const, attachmentId: uploaded.attachment_id }
                        : attachment
                ));
            } catch (error) {
                const message = error instanceof AttachmentUploadError
                    ? error.message
                    : "Upload failed. Please check your connection and try again.";
                setPendingAttachments((previous) => previous.map((attachment) =>
                    attachment.localId === localId
                        ? { ...attachment, status: "error" as const, error: message }
                        : attachment
                ));
            }
        }
    };

    const handleRemoveAttachment = (localId: string) => {
        setPendingAttachments((previous) => {
            const target = previous.find((attachment) => attachment.localId === localId);
            if (target) URL.revokeObjectURL(target.previewUrl);
            return previous.filter((attachment) => attachment.localId !== localId);
        });
    };

    const pendingAttachmentsRef = useRef<PendingAttachment[]>([]);
    useEffect(() => {
        pendingAttachmentsRef.current = pendingAttachments;
    }, [pendingAttachments]);

    // Revoke every remaining preview object URL on unmount - real browser
    // memory (createObjectURL), not freed just because the component
    // unmounts. Deliberately a separate effect with empty deps (runs its
    // cleanup exactly once, on unmount) reading the ref above for the
    // latest list, not the stale empty array a `[pendingAttachments]`-keyed
    // cleanup would otherwise capture.
    useEffect(() => () => {
        pendingAttachmentsRef.current.forEach((attachment) => URL.revokeObjectURL(attachment.previewUrl));
    }, []);

    const hasUploadingAttachment = pendingAttachments.some((attachment) => attachment.status === "uploading");
    const hasErroredAttachment = pendingAttachments.some((attachment) => attachment.status === "error");
    const attachmentsBlockSend = hasUploadingAttachment || hasErroredAttachment
        || (pendingAttachments.length > 0 && !selectedModelSupportsVision);

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (submiting) return;
        const formData = new FormData(event.currentTarget);
        const model = effectiveModel;
        const prompt = formData.get("prompt") as string;
        const selectedScope = contractSelection !== ALL_CONTRACTS_VALUE ? contractSelection : null;
        const contract_id = activeSession ? activeSession.contract_id : selectedScope;

        if (!prompt.trim() || !model || attachmentsBlockSend) {
            return;
        }

        const readyAttachments = pendingAttachments.filter(
            (attachment): attachment is PendingAttachment & { attachmentId: string } =>
                attachment.status === "ready" && Boolean(attachment.attachmentId),
        );

        setSubmiting(true);

        // Lazy session creation: "New chat" doesn't POST until the first
        // real message actually sends, so an unused click never leaves an
        // empty thread in the switcher. If an attachment was made first,
        // ensureSession() (called from handleFilesSelected) already created
        // one and activeSession is set - this just reuses it.
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
            parts: [
                // Attachments render first (Claude/ChatGPT convention: image
                // strip above the text) - same synthetic-part shape
                // sessionMessages.ts produces on restore, so live-sent and
                // restored messages render identically.
                ...(readyAttachments.length ? [{
                    type: "attachments" as const,
                    content: JSON.stringify(readyAttachments.map((attachment) => ({
                        attachment_id: attachment.attachmentId,
                        mime_type: attachment.file.type,
                    }))),
                }] : []),
                { content: prompt, type: "user_message" as const },
            ],
            generating: false,
            verifying: false
        };

        const aiMessage: Message = {
            id: Date.now().toString() + "ai",
            type: "ai",
            parts: [],
            generating: true,
            verifying: false
        };

        addMessage(userMessage);
        addMessage(aiMessage);
        // removed console log

        // Clear the form after submission
        setPromptValue('');
        // Preview URLs are local browser memory (createObjectURL), not
        // needed once the real, authenticated, session-scoped fetch
        // (AttachmentImage) takes over rendering for this now-sent message.
        pendingAttachments.forEach((attachment) => URL.revokeObjectURL(attachment.previewUrl));
        setPendingAttachments([]);
        setAttachmentsError(null);

        const auth = authHeader();
        const runId = crypto.randomUUID();
        const cancelServer = async () => {
            const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(auth ? { Authorization: auth } : {}),
                },
                body: JSON.stringify({ session_id: session.session_id }),
            });
            if (response.status === 202) return true;
            if (response.status === 404 || response.status === 409) return false;
            throw new Error('Chat cancellation could not be confirmed');
        };
        const { id: requestId, controller } = beginRequest(
            session.session_id,
            aiMessage.id,
            cancelServer,
        );
        let streamFinished = false;
        let errorReported = false;
        try {
            await fetchEventSource('/api/run/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(auth ? { Authorization: auth } : {}),
                },
                body: JSON.stringify({
                    model, prompt, contract_id, session_id: session.session_id, run_id: runId,
                    ...(readyAttachments.length
                        ? { attachment_ids: readyAttachments.map((attachment) => attachment.attachmentId) }
                        : {}),
                }),
                signal: controller.signal,
                onmessage(event) {
                    if (controller.signal.aborted || streamFinished) return;
                    const data: MessagePart = JSON.parse(event.data);

                    // The backend emits terminal answer/error content only after
                    // durable persistence. From this point completion wins the
                    // race and Stop is no longer meaningful, even if the final
                    // `end` event has not reached the browser yet.
                    if ((data.type === "ai_message" || data.type === "error") && data.status) {
                        markRequestCommitted(requestId);
                    }
                    if (data.type === "end") {
                        streamFinished = true;
                        updateMessageGenerating(aiMessage.id, false);
                        finishRequest(requestId);
                        setSubmiting(false);
                    } else if (data.type === "history") {
                        // The backend persists every turn; history is restored
                        // from the authenticated session detail endpoint.
                    } else if (data.type === "status" && data.phase === "verifying") {
                        // Generation itself is done; Output Guard's audit step
                        // has started. A distinct, visible phase so a
                        // legitimate multi-second wait here doesn't look like
                        // a stuck spinner.
                        updateMessageVerifying(aiMessage.id, true);
                    } else {
                        addMessagePart(aiMessage.id, data);
                    }
                },
                async onopen(response) {
                    if (response.status === 401) {
                        clearSession();
                        throw new Error('Session expired - please sign in again');
                    }
                },
                onclose() {
                    streamFinished = true;
                },
                onerror(error) {
                    if (controller.signal.aborted) throw error;
                    if (!errorReported) {
                        errorReported = true;
                        addMessagePart(aiMessage.id, {
                            type: "error",
                            status: "generation_failed",
                            content: "Response failed before completion. Please retry.",
                        });
                        updateMessageGenerating(aiMessage.id, false);
                    }
                    throw error;
                }
            });
        } catch {
            if (!controller.signal.aborted && !streamFinished && !errorReported) {
                addMessagePart(aiMessage.id, {
                    type: "error",
                    status: "generation_failed",
                    content: "Response failed before completion. Please retry.",
                });
                updateMessageGenerating(aiMessage.id, false);
            }
        } finally {
            streamFinished = true;
            finishRequest(requestId);
            setSubmiting(false);
        }
    };

    const handleClear = (event: MouseEvent) => {
        event.preventDefault();
        void stopActiveRequest();
        setSubmiting(false);
        reset();
        setPromptValue('');
        pendingAttachments.forEach((attachment) => URL.revokeObjectURL(attachment.previewUrl));
        setPendingAttachments([]);
        setAttachmentsError(null);
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
    const hasExplicitScopeSelection = React.useRef(false);
    React.useEffect(() => {
        if (!activeSession && hasExplicitScopeSelection.current) return;
        setContractSelection(effectiveContractId);
        if (activeSession) hasExplicitScopeSelection.current = false;
    }, [activeSession, effectiveContractId]);

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
            submiting ||
            !effectiveModel
        ) return;
        consumePromptRequest(pendingAutoSubmitId);
        setPendingAutoSubmitId(null);
        formRef.current?.requestSubmit();
    }, [consumePromptRequest, effectiveModel, pendingAutoSubmitId, promptRequest, promptValue, submiting]);

    React.useEffect(() => () => {
        void stopActiveRequest();
    }, [stopActiveRequest]);

    const handleContractChange = (value: string) => {
        hasExplicitScopeSelection.current = true;
        setContractSelection(value);
        setSelectedContract(value === ALL_CONTRACTS_VALUE ? null : value);
    };

    return (
        <div className="flex-0">
            <form ref={formRef} className="flex flex-col gap-2 relative" onSubmit={handleSubmit}>
                {pendingAttachments.length > 0 && (
                    <div className="flex flex-wrap gap-2" aria-label="Attached images to send">
                        {pendingAttachments.map((attachment) => (
                            <div key={attachment.localId} className="relative">
                                <div
                                    className={`h-16 w-16 overflow-hidden rounded border ${attachment.status === "error" ? "border-red-400" : "border-input"}`}
                                >
                                    <img
                                        src={attachment.previewUrl}
                                        alt=""
                                        className={`h-full w-full object-cover ${attachment.status === "uploading" ? "opacity-50" : ""}`}
                                    />
                                    {attachment.status === "uploading" && (
                                        <div className="absolute inset-0 flex items-center justify-center" aria-live="polite">
                                            <LoaderCircle className="h-5 w-5 animate-spin text-foreground" aria-label="Uploading" />
                                        </div>
                                    )}
                                    {attachment.status === "error" && (
                                        <div
                                            className="absolute inset-0 flex items-center justify-center bg-red-50/80"
                                            role="alert"
                                            title={attachment.error}
                                        >
                                            <AlertCircle className="h-5 w-5 text-red-700" aria-label={attachment.error || "Upload failed"} />
                                        </div>
                                    )}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => handleRemoveAttachment(attachment.localId)}
                                    className="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 text-foreground shadow hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                                    aria-label={`Remove attached image ${attachment.file.name}`}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                                {attachment.status === "error" && (
                                    <p className="mt-1 max-w-16 text-[10px] leading-tight text-red-700">{attachment.error}</p>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                <Textarea
                    name="prompt"
                    className="m-0 max-h-[400px]"
                    placeholder="Type your prompt here!"
                    onKeyDown={handleKeyDown}
                    ref={textareaRef}
                    value={promptValue}
                    onChange={(event) => setPromptValue(event.target.value)}
                    disabled={Boolean(activeRequest)}
                />
                <div className="flex flex-wrap items-center gap-2">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        multiple
                        className="hidden"
                        onChange={(event) => void handleFilesSelected(event)}
                    />
                    <Button
                        type="button"
                        variant="outline"
                        className="flex-0"
                        onClick={handleAttachClick}
                        disabled={Boolean(activeRequest) || pendingAttachments.length >= MAX_ATTACHMENTS_PER_MESSAGE}
                        aria-label="Attach an image"
                    >
                        <Paperclip aria-hidden="true" />
                        Attach image
                    </Button>
                    {attachmentsError && <p className="text-xs text-red-700" role="alert">{attachmentsError}</p>}
                    {pendingAttachments.length > 0 && !selectedModelSupportsVision && (
                        <p className="text-xs text-red-700" role="alert">
                            {selectedModelOption?.display_label || "The selected model"} does not support image attachments - switch to a vision-capable model to send.
                        </p>
                    )}
                </div>
                <div className="flex flex-col gap-2 md:flex-row">
                    <div className="flex flex-1 flex-col gap-1">
                        <span className="text-xs font-medium text-muted-foreground">Contract scope</span>
                        <Select
                            name="contract_id"
                            value={contractSelection}
                            onValueChange={handleContractChange}
                            disabled={Boolean(activeSession) || Boolean(activeRequest)}
                        >
                            <SelectTrigger className="text-foreground" aria-label="Contract scope">
                                <SelectValue placeholder="Which contract?" />
                            </SelectTrigger>
                            <SelectContent position="popper" side="right" align="start" sideOffset={8} collisionPadding={12} className="max-h-[8rem] data-[side=left]:translate-y-2 data-[side=right]:translate-y-2">
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
                    <div className="flex min-w-0 flex-col gap-1">
                        <span className="text-xs font-medium text-muted-foreground">Model</span>
                        <Select name="model" value={effectiveModel} onValueChange={setSelectedModel} disabled={Boolean(activeRequest) || !models.length}>
                            <SelectTrigger className="w-full text-foreground" aria-label="Model">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent position="popper" side="left" align="start" sideOffset={8} collisionPadding={12} className="max-h-[8rem] data-[side=left]:translate-y-2 data-[side=right]:translate-y-2">
                                <SelectGroup>
                                    {models.map((model) => (
                                        <SelectItem key={model.id} value={model.id}>{model.display_label}</SelectItem>
                                    ))}
                                </SelectGroup>
                            </SelectContent>
                        </Select>
                    </div>
                    {modelError && <p className="text-xs text-red-700" role="alert">{modelError}</p>}
                    <Button variant="outline" className="flex-0" onClick={handleClear} disabled={Boolean(activeRequest)}>
                        Reset
                    </Button>
                    {activeRequest?.phase === "running" ? (
                        <Button
                            className="flex-0 bg-red-700 text-white hover:bg-red-800 focus-visible:ring-red-600"
                            type="button"
                            aria-label="Stop generating"
                            onClick={() => void stopActiveRequest()}
                        >
                            Stop generating
                            <Square aria-hidden="true" />
                        </Button>
                    ) : activeRequest ? (
                        <Button className="flex-0" type="button" disabled aria-live="polite">
                            {activeRequest.phase === "stopping" ? "Stopping…" : "Finishing…"}
                            <LoaderCircle className="animate-spin" aria-hidden="true" />
                        </Button>
                    ) : (
                        <Button className="flex-0" type="submit" disabled={submiting || !effectiveModel || attachmentsBlockSend}>
                            Send your prompt now!
                            <SendHorizontal aria-hidden="true" />
                        </Button>
                    )}
                </div>
            </form>
        </div>
    );
}
