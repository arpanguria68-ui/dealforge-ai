import { Quote } from 'lucide-react';
import { sourceDomId, type SourceItem } from '@/lib/citations';

function hostOf(source: string): string | null {
    try {
        return new URL(source).hostname.replace(/^www\./, '');
    } catch {
        return null;
    }
}

/** Superscript pill for an inline citation; jumps to and opens its source card. */
export function CitationPill({ n, msgId, item }: { n: number; msgId: string; item?: SourceItem }) {
    function jump() {
        const el = document.getElementById(sourceDomId(msgId, n));
        if (!el) return;
        const details = el.closest('details');
        if (details) details.open = true;
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        el.classList.add('ring-1', 'ring-cyan-400/60');
        window.setTimeout(() => el.classList.remove('ring-1', 'ring-cyan-400/60'), 1600);
    }
    return (
        <button
            type="button"
            onClick={jump}
            title={item ? `${item.label}${item.source ? ` — ${item.source}` : ''}` : `Source ${n}`}
            aria-label={`Source ${n}${item ? `: ${item.label}` : ''}`}
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded bg-cyan-500/15 px-1 align-baseline text-[10px] font-semibold text-cyan-700 transition-colors hover:bg-cyan-500/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
        >
            {n}
        </button>
    );
}

/** Numbered source cards, open by default (answer first, evidence right under it). */
export function SourceCards({ items, msgId }: { items: SourceItem[]; msgId: string }) {
    if (items.length === 0) return null;
    return (
        <details open className="rounded-xl border border-border bg-muted/60 overflow-hidden">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-muted-foreground hover:text-foreground">
                <Quote className="h-3 w-3 text-cyan-700" />
                Sources ({items.length})
            </summary>
            <ol className="grid gap-2 px-3 pb-3 sm:grid-cols-2">
                {items.map((item, i) => {
                    const host = hostOf(item.source);
                    return (
                        <li
                            key={i}
                            id={sourceDomId(msgId, i + 1)}
                            className="flex items-start gap-2 rounded-lg border border-border bg-muted/60 p-2.5 text-[11px] leading-relaxed text-muted-foreground transition-shadow"
                        >
                            <span className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded bg-cyan-500/15 text-[9px] font-bold text-cyan-700">{i + 1}</span>
                            <span className="min-w-0">
                                <span className="block truncate font-semibold text-foreground" title={item.label}>{item.label}</span>
                                {host ? (
                                    <a href={item.source} target="_blank" rel="noreferrer" className="block truncate text-cyan-700 hover:text-cyan-700 hover:underline">{host}</a>
                                ) : (
                                    item.source && <span className="block break-words text-muted-foreground/70">{item.source}</span>
                                )}
                            </span>
                        </li>
                    );
                })}
            </ol>
        </details>
    );
}
