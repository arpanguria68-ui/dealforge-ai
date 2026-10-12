export interface SourceItem {
    label: string;
    source: string;
}

const CITE_PREFIX = '#cite:';

/** DOM id of a source card, shared by the card and the inline pill that points at it. */
export function sourceDomId(msgId: string, n: number): string {
    return `source-${msgId}-${n}`;
}

/**
 * Turn `[1]` style markers into markdown links (`[1](#cite:1)`) so the renderer can
 * draw them as citation pills. Only numbers that have a matching source are linked,
 * and text inside fenced code blocks is left alone.
 */
export function linkCitations(text: string, count: number): string {
    if (count <= 0) return text;
    return text
        .split(/(```[\s\S]*?```)/g)
        .map((part, i) => (i % 2 === 1
            ? part
            : part.replace(/\[(\d{1,2})\](?!\()/g, (whole, digits: string) => {
                const n = Number(digits);
                return n >= 1 && n <= count ? `[${n}](${CITE_PREFIX}${n})` : whole;
            })))
        .join('');
}

export function citationIndex(href: string | undefined): number | null {
    if (!href || !href.startsWith(CITE_PREFIX)) return null;
    const n = Number(href.slice(CITE_PREFIX.length));
    return Number.isInteger(n) && n > 0 ? n : null;
}
