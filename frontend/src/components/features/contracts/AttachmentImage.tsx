import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { chatAttachmentApi } from "../../../services/chatAttachmentApi";
import { Button } from "../../shared/ui/button";

interface Props {
    sessionId: string;
    attachmentId: string;
}

// Renders one previously-uploaded chat image attachment - live-sent or
// restored from history, same component either way. Authenticated fetch
// only (same posture as PdfCitationViewer.tsx's source fetch): never a
// public URL, never a bare <img src="/api/..."> (which wouldn't carry the
// bearer token at all).
export function AttachmentImage({ sessionId, attachmentId }: Props) {
    const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");
    const [objectUrl, setObjectUrl] = useState<string | null>(null);
    const [lightboxOpen, setLightboxOpen] = useState(false);

    useEffect(() => {
        let disposed = false;
        let url: string | null = null;
        setStatus("loading");
        void (async () => {
            try {
                url = await chatAttachmentApi.fetchImageObjectUrl(sessionId, attachmentId);
                if (disposed) {
                    URL.revokeObjectURL(url);
                    return;
                }
                setObjectUrl(url);
                setStatus("ready");
            } catch {
                if (!disposed) setStatus("unavailable");
            }
        })();
        return () => {
            disposed = true;
            if (url) URL.revokeObjectURL(url);
        };
    }, [sessionId, attachmentId]);

    useEffect(() => {
        if (!lightboxOpen) return;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") setLightboxOpen(false);
        };
        window.addEventListener("keydown", closeOnEscape);
        return () => window.removeEventListener("keydown", closeOnEscape);
    }, [lightboxOpen]);

    if (status === "loading") {
        return <div className="h-24 w-24 animate-pulse rounded-md border bg-muted" aria-label="Loading attached image" />;
    }
    if (status === "unavailable") {
        return (
            <div role="alert" className="flex h-24 w-24 items-center justify-center rounded-md border border-red-200 bg-red-50 p-2 text-center text-xs text-red-700">
                Image unavailable
            </div>
        );
    }
    return (
        <>
            <button
                type="button"
                onClick={() => setLightboxOpen(true)}
                className="block overflow-hidden rounded-md border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                aria-label="Open attached image"
            >
                <img src={objectUrl!} alt="" className="h-24 w-24 object-cover" />
            </button>
            {lightboxOpen && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
                    role="dialog"
                    aria-modal="true"
                    aria-label="Attached image, full size"
                    onClick={() => setLightboxOpen(false)}
                >
                    <img src={objectUrl!} className="max-h-full max-w-full rounded-lg object-contain shadow-2xl" alt="Attached, full size" />
                    <Button
                        type="button"
                        variant="outline"
                        className="absolute right-4 top-4"
                        onClick={() => setLightboxOpen(false)}
                        aria-label="Close image"
                    >
                        <X />
                    </Button>
                </div>
            )}
        </>
    );
}
