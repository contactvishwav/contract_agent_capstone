function normalize(value: string): string {
    return value.normalize("NFKC").replace(/\s+/g, " ").trim().toLocaleLowerCase();
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
