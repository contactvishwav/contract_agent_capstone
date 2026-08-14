function normalize(value: string): string {
    // Strip punctuation (quote-style mismatches, dash variants, stray
    // hyphens from PDF line-break artifacts) before collapsing whitespace -
    // pdf.js's text-layer extraction and the backend's stored excerpt text
    // frequently differ only in these characters, not in actual wording.
    // \p{L}/\p{N} (Unicode letters/numbers) kept, everything else that
    // isn't whitespace is dropped.
    return value
        .normalize("NFKC")
        .replace(/[^\p{L}\p{N}\s]/gu, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLocaleLowerCase();
}

/** Return text-layer items only for one deterministic, unambiguous match. */
export function uniqueHighlightItemIndexes(items: string[], target: string): Set<number> {
    const normalizedTarget = normalize(target);
    if (normalizedTarget.length < 12) return new Set();

    const ranges: Array<{ start: number; end: number }> = [];
    let joined = "";
    items.forEach((item) => {
        const normalizedItem = normalize(item);
        if (!normalizedItem) {
            ranges.push({ start: joined.length, end: joined.length });
            return;
        }
        if (joined) joined += " ";
        const start = joined.length;
        joined += normalizedItem;
        ranges.push({ start, end: joined.length });
    });

    const occurrences: number[] = [];
    let cursor = 0;
    while (cursor <= joined.length - normalizedTarget.length) {
        const found = joined.indexOf(normalizedTarget, cursor);
        if (found < 0) break;
        occurrences.push(found);
        if (occurrences.length > 1) return new Set();
        cursor = found + 1;
    }
    if (occurrences.length !== 1) return new Set();

    const start = occurrences[0];
    const end = start + normalizedTarget.length;
    return new Set(
        ranges.flatMap((range, index) => (
            range.end > range.start && range.end > start && range.start < end ? [index] : []
        )),
    );
}
