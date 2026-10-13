import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';

interface CodeBlockProps {
    className?: string;
    children?: ReactNode;
}

/** Fenced code block with a language label and a copy button. */
export function CodeBlock({ className, children }: CodeBlockProps) {
    const [copied, setCopied] = useState(false);
    const timer = useRef<number | undefined>(undefined);
    const text = String(children ?? '').replace(/\n$/, '');
    const language = /language-([\w+#-]+)/.exec(className || '')?.[1];

    useEffect(() => () => window.clearTimeout(timer.current), []);

    async function copy() {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setCopied(false), 1500);
        } catch {
            // Clipboard can be unavailable (insecure context, denied permission); nothing to recover.
        }
    }

    return (
        <div className="my-2 min-w-[min(20rem,100%)] overflow-hidden rounded-lg border border-border bg-foreground/30">
            <div className="flex items-center justify-between gap-4 border-b border-border px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                <span>{language || 'code'}</span>
                <button
                    type="button"
                    onClick={copy}
                    aria-label={copied ? 'Copied' : 'Copy code'}
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 normal-case text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
                >
                    {copied ? <Check className="h-3 w-3 text-emerald-700" /> : <Copy className="h-3 w-3" />}
                    {copied ? 'Copied' : 'Copy'}
                </button>
            </div>
            <pre className="overflow-x-auto p-3 text-xs text-emerald-700">
                <code className={className}>{children}</code>
            </pre>
        </div>
    );
}
