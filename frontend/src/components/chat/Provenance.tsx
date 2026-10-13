import { AlertTriangle, ShieldCheck } from 'lucide-react';

interface Grounding {
    status?: string;
    figures_total?: number;
    figures_grounded?: number;
    ungrounded?: string[];
    unsupported_sources?: string[];
}

interface ReconciliationIssue {
    metric?: string;
    message?: string;
}

interface ValuationAudit {
    sources?: string[];
    assumptions?: string[];
    missing?: string[];
    wacc_build?: Record<string, number>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
    Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const strings = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];

/**
 * What the backend verified about an agent result: how many memo figures trace to
 * recorded evidence, and the sources / assumptions / gaps behind a valuation.
 * Renders nothing when the result carries neither.
 */
export function ProvenanceBar({ data }: { data: unknown }) {
    if (!isRecord(data)) return null;
    const grounding = isRecord(data.grounding) ? (data.grounding as Grounding) : null;
    const audit = isRecord(data.audit) ? (data.audit as ValuationAudit) : null;
    const hasGrounding = Boolean(grounding && grounding.status && grounding.status !== 'no_sources'
        && typeof grounding.figures_total === 'number');
    const auditSources = strings(audit?.sources);
    const assumptions = strings(audit?.assumptions);
    const missing = strings(audit?.missing);
    const hasAudit = auditSources.length + assumptions.length + missing.length > 0;
    const reconciliation = Array.isArray(data.reconciliation)
        ? (data.reconciliation as ReconciliationIssue[]).filter(issue => issue && typeof issue.message === 'string')
        : [];
    if (!hasGrounding && !hasAudit && reconciliation.length === 0) return null;

    const ok = grounding?.status === 'ok';
    const ungrounded = strings(grounding?.ungrounded);
    const vendors = strings(grounding?.unsupported_sources);

    return (
        <div className="mt-2 w-full space-y-1.5 text-[11px]">
            {reconciliation.length > 0 && (
                <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.05] px-3 py-2 text-amber-200/90" role="alert">
                    <p className="flex items-center gap-1.5 font-medium text-amber-300">
                        <AlertTriangle className="h-3.5 w-3.5" /> Agents disagree: resolve before relying on these figures
                    </p>
                    <ul className="mt-1 list-disc pl-5 text-white/60">
                        {reconciliation.map((issue, i) => <li key={i}>{issue.message}</li>)}
                    </ul>
                </div>
            )}
            {hasGrounding && grounding && (
                <details className={`rounded-xl border ${ok ? 'border-emerald-500/20 bg-emerald-500/[0.04]' : 'border-amber-500/25 bg-amber-500/[0.05]'} overflow-hidden`}>
                    <summary className={`flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 font-medium ${ok ? 'text-emerald-300' : 'text-amber-300'}`}>
                        {ok ? <ShieldCheck className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
                        {grounding.figures_grounded} of {grounding.figures_total} figures match recorded evidence
                        {!ok && <span className="text-amber-300/70"> — review before relying on it</span>}
                    </summary>
                    <div className="space-y-1 px-3 pb-2.5 text-white/55">
                        {ungrounded.length > 0 && <p><span className="text-white/75">Not found in evidence:</span> {ungrounded.slice(0, 15).join(', ')}</p>}
                        {vendors.length > 0 && <p><span className="text-white/75">Cited with no tool output:</span> {vendors.join(', ')}</p>}
                        {ungrounded.length === 0 && vendors.length === 0 && <p>Every figure traces to an agent output.</p>}
                    </div>
                </details>
            )}
            {hasAudit && (
                <details className="rounded-xl border border-white/[0.06] bg-white/[0.02] overflow-hidden">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 font-medium text-white/50 hover:text-white/80">
                        <ShieldCheck className="h-3.5 w-3.5 text-cyan-400" />
                        Inputs &amp; assumptions
                    </summary>
                    <div className="space-y-1.5 px-3 pb-2.5 text-white/55">
                        {auditSources.length > 0 && <p><span className="text-white/75">Data sources:</span> {auditSources.join('; ')}</p>}
                        {audit?.wacc_build && (
                            <p><span className="text-white/75">WACC build:</span>{' '}
                                {Object.entries(audit.wacc_build).map(([k, v]) => `${k.replace(/_/g, ' ')} ${typeof v === 'number' ? v : ''}`).join(' · ')}
                            </p>
                        )}
                        {assumptions.length > 0 && (
                            <div><span className="text-white/75">Assumptions (not observed data):</span>
                                <ul className="mt-0.5 list-disc pl-4">{assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
                            </div>
                        )}
                        {missing.length > 0 && <p className="text-amber-300/80"><span className="text-amber-300">Not retrieved:</span> {missing.join('; ')}</p>}
                    </div>
                </details>
            )}
        </div>
    );
}
