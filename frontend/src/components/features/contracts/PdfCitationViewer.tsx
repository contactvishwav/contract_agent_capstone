import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Minus, Plus, X } from "lucide-react";
import {
    getDocument,
    GlobalWorkerOptions,
    Util,
} from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { apiFetch } from "../../../lib/apiClient";
import { ChatCitation } from "../../../services/chatSessionApi";
import { Button } from "../../shared/ui/button";
import { uniqueHighlightItemIndexes } from "./pdfHighlight";

GlobalWorkerOptions.workerSrc = workerUrl;

interface Props {
    citation: ChatCitation;
    onClose: () => void;
}

type PositionedText = {
    text: string;
    left: number;
    top: number;
    width: number;
    height: number;
    angle: number;
    highlighted: boolean;
};

export function PdfCitationViewer({ citation, onClose }: Props) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const highlightRef = useRef<HTMLSpanElement>(null);
    const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
    const [pageNumber, setPageNumber] = useState(citation.page ?? 1);
    const [scale, setScale] = useState(1.25);
    const [textItems, setTextItems] = useState<PositionedText[]>([]);
    const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
    const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");
    const [highlightVerified, setHighlightVerified] = useState(false);

    useEffect(() => {
        let disposed = false;
        let loaded: PDFDocumentProxy | null = null;
        void (async () => {
            try {
                const response = await apiFetch(
                    `/api/documents/${encodeURIComponent(citation.contract_id)}/source`,
                    { cache: "no-store" },
                );
                if (!response.ok || response.headers.get("content-type")?.split(";")[0] !== "application/pdf") {
                    throw new Error("Source unavailable");
                }
                const task = getDocument({
                    data: new Uint8Array(await response.arrayBuffer()),
                    standardFontDataUrl: "/pdfjs/standard_fonts/",
                    cMapUrl: "/pdfjs/cmaps/",
                    cMapPacked: true,
                });
                loaded = await task.promise;
                if (disposed) {
                    await loaded.destroy();
                    return;
                }
                setPdf(loaded);
                setPageNumber(Math.min(Math.max(citation.page ?? 1, 1), loaded.numPages));
                setStatus("ready");
            } catch {
                if (!disposed) setStatus("unavailable");
            }
        })();
        return () => {
            disposed = true;
            if (loaded) void loaded.destroy();
        };
    }, [citation.contract_id, citation.page]);

    useEffect(() => {
        if (!pdf || !canvasRef.current) return;
        let cancelled = false;
        let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
        void (async () => {
            const page = await pdf.getPage(pageNumber);
            if (cancelled || !canvasRef.current) return;
            const viewport = page.getViewport({ scale });
            const outputScale = window.devicePixelRatio || 1;
            const canvas = canvasRef.current;
            const context = canvas.getContext("2d");
            if (!context) return;
            canvas.width = Math.floor(viewport.width * outputScale);
            canvas.height = Math.floor(viewport.height * outputScale);
            canvas.style.width = `${viewport.width}px`;
            canvas.style.height = `${viewport.height}px`;
            setViewportSize({ width: viewport.width, height: viewport.height });

            renderTask = page.render({
                canvas,
                canvasContext: context,
                viewport,
                transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
            });
            await renderTask.promise;
            if (cancelled) return;

            const textContent = await page.getTextContent();
            const text = textContent.items.filter((item): item is typeof item & {
                str: string;
                transform: number[];
                width: number;
            } => "str" in item);
            const highlighted = citation.provenance_status === "exact" && citation.highlight_text
                ? uniqueHighlightItemIndexes(text.map((item) => item.str), citation.highlight_text)
                : new Set<number>();
            setHighlightVerified(highlighted.size > 0);
            setTextItems(text.map((item, index) => {
                const transform = Util.transform(viewport.transform, item.transform);
                const angle = Math.atan2(transform[1], transform[0]);
                const height = Math.hypot(transform[2], transform[3]);
                return {
                    text: item.str,
                    left: transform[4],
                    top: transform[5] - height,
                    width: item.width * scale,
                    height,
                    angle,
                    highlighted: highlighted.has(index),
                };
            }));
        })().catch((error) => {
            if (!cancelled && error?.name !== "RenderingCancelledException") setStatus("unavailable");
        });
        return () => {
            cancelled = true;
            renderTask?.cancel();
        };
    }, [citation.highlight_text, citation.provenance_status, pageNumber, pdf, scale]);

    useEffect(() => {
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") onClose();
        };
        window.addEventListener("keydown", closeOnEscape);
        return () => window.removeEventListener("keydown", closeOnEscape);
    }, [onClose]);

    useEffect(() => {
        if (highlightVerified) {
            highlightRef.current?.scrollIntoView({ block: "center", inline: "center" });
            highlightRef.current?.focus({ preventScroll: true });
        }
    }, [highlightVerified, pageNumber]);

    const locator = useMemo(
        () => citation.page ? `page ${citation.page}` : "source document",
        [citation.page],
    );

    return (
        <div className="fixed inset-0 z-50 flex flex-col bg-slate-950/80 p-2 md:p-5" role="dialog" aria-modal="true" aria-label={`Source: ${citation.filename}, ${locator}`}>
            <div className="mx-auto flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl">
                <header className="flex flex-wrap items-center gap-2 border-b p-3">
                    <div className="min-w-0 flex-1">
                        <h2 className="truncate font-semibold">{citation.filename}</h2>
                        <p className="text-xs text-muted-foreground">
                            {citation.page ? `Verified source page ${citation.page}` : "Source document"}
                        </p>
                    </div>
                    <Button type="button" variant="outline" disabled={!pdf || pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)} aria-label="Previous page"><ChevronLeft /></Button>
                    <span className="text-sm" aria-live="polite">{pageNumber} / {pdf?.numPages ?? "—"}</span>
                    <Button type="button" variant="outline" disabled={!pdf || pageNumber >= pdf.numPages} onClick={() => setPageNumber((value) => value + 1)} aria-label="Next page"><ChevronRight /></Button>
                    <Button type="button" variant="outline" onClick={() => setScale((value) => Math.max(0.75, value - 0.25))} aria-label="Zoom out"><Minus /></Button>
                    <Button type="button" variant="outline" onClick={() => setScale((value) => Math.min(3, value + 0.25))} aria-label="Zoom in"><Plus /></Button>
                    <Button type="button" variant="outline" onClick={onClose} autoFocus aria-label="Close source viewer"><X /></Button>
                </header>
                {citation.excerpt && (
                    <div className="border-b bg-amber-50 px-4 py-2 text-sm">
                        <span className="font-medium">Cited passage: </span>{citation.excerpt}
                        {citation.provenance_status === "exact" && !highlightVerified && status === "ready" && (
                            <span className="ml-2 text-amber-800">Page verified; exact text-layer highlight unavailable.</span>
                        )}
                        {citation.provenance_status === "page_only" && (
                            <span className="ml-2 text-amber-800">Page verified; exact highlight not claimed.</span>
                        )}
                    </div>
                )}
                <div className="flex-1 overflow-auto bg-slate-200 p-3" tabIndex={0} aria-label="PDF page canvas and searchable text layer">
                    {status === "loading" && <p className="p-6 text-center">Loading authenticated source…</p>}
                    {status === "unavailable" && <p role="alert" className="p-6 text-center">This source is unavailable or you no longer have access.</p>}
                    <div className="relative mx-auto bg-white shadow" style={{ width: viewportSize.width, height: viewportSize.height }}>
                        <canvas ref={canvasRef} aria-label={`PDF page ${pageNumber}`} />
                        <div className="absolute inset-0 overflow-hidden" aria-label="Selectable PDF text layer">
                            {textItems.map((item, index) => (
                                <span
                                    key={`${index}-${item.left}-${item.top}`}
                                    ref={item.highlighted && !textItems.slice(0, index).some((prior) => prior.highlighted) ? highlightRef : undefined}
                                    tabIndex={item.highlighted ? -1 : undefined}
                                    className={item.highlighted ? "absolute bg-yellow-300/70 text-transparent mix-blend-multiply" : "absolute text-transparent selection:bg-blue-300/50"}
                                    style={{
                                        left: item.left,
                                        top: item.top,
                                        width: Math.max(item.width, 1),
                                        height: Math.max(item.height, 1),
                                        fontSize: item.height,
                                        lineHeight: 1,
                                        transform: `rotate(${item.angle}rad)`,
                                        transformOrigin: "0 0",
                                        whiteSpace: "pre",
                                    }}
                                >{item.text}</span>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
