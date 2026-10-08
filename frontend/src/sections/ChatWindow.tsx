import { useState, useRef, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
    Send, Paperclip, User, Loader2, Sparkles,
    TrendingUp, AlertTriangle, FileText, Scale, Brain,
    Download, ChevronRight, HelpCircle, Cpu, Cloud,
    ListChecks, CheckCircle2, Copy, Check, RotateCcw, Edit2,
    Zap, Sliders, Star, Globe, BookOpen, Database, ChevronDown, ChevronUp,
    ThumbsUp, ThumbsDown, Quote
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from '@/components/chat/CodeBlock';
import { StarterPrompts } from '@/components/chat/EmptyState';
import { useDealForgeStore } from '@/lib/dealforge-store';
import { API_BASE, withAdminAuth } from '@/lib/api-base';
import { isDealAnalysisRequest } from '@/lib/chat-intent';

// Stable fallback for stored messages without a timestamp (keeps rendering pure).
const FALLBACK_TIMESTAMP = Date.now();

// ─── Types ───

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'agent' | 'system';
    content: string;
    agentName?: string;
    timestamp: Date;
    status?: 'thinking' | 'done' | 'error';
    provider?: string;
    dealId?: string;
    metadata?: MessageMetadata;
    followUps?: string[];
    missingData?: string[];
}

interface MessageMetadata extends Record<string, unknown> {
    action_id?: number;
    _agentSummary?: string;
    _agentDetail?: string;
    pending_clarification?: boolean;
    is_assumptions_summary?: boolean;
    questions?: ClarificationQuestion[];
    deal_id?: string;
    original_prompt?: string;
    clarification_round?: number;
    qa_controls?: { can_ask_more?: boolean };
    citations?: unknown;
    confidence?: number;
}

interface ClarificationQuestion {
    question: string;
    reasoning?: string;
}

interface ClarificationAnswer {
    question: string;
    answer: string;
}

interface TaskPlanItem {
    id?: string;
    title: string;
    description: string;
    assigned_agent: string;
    priority?: string;
    status?: string;
    result?: unknown;
    depends_on?: string[];
}

interface AgentResult extends Record<string, unknown> {
    _agent_type?: string;
    agent?: string;
    reasoning?: string;
    confidence?: number;
    execution_time_ms?: number;
    provider?: string;
    success?: boolean;
    error?: string;
    action_id?: number;
    data?: Record<string, unknown>;
}

interface TaskListRecord {
    id: string;
    title?: string;
    company_name?: string;
    status?: string;
    created_at?: string;
    items?: TaskPlanItem[];
}

interface DocumentPlanResponse {
    title: string;
    formats: string[];
    audience: string;
    sections: { key: string; title: string }[];
    gaps: string[];
    gap_agents: string[];
    assumptions: string[];
    questions: string[];
}

interface PlanResponse {
    mode?: 'document';
    reasoning?: string;
    data?: {
        todo_list?: TaskListRecord;
        laya_decision?: { selected_agent?: string };
        document_plan?: DocumentPlanResponse;
        document_request?: { request: string };
    };
}

function buildExecutionWaves(tasks: TaskPlanItem[], maxParallel = 3): TaskPlanItem[][] {
    const taskIds = new Set(tasks.map(task => task.id).filter((id): id is string => Boolean(id)));
    for (const task of tasks) {
        for (const dependency of task.depends_on || []) {
            if (!taskIds.has(dependency)) throw new Error(`Plan dependency ${dependency} is missing from the task list.`);
        }
    }
    const pending = tasks.map((task, index) => ({ task, key: task.id || `index:${index}` }));
    const completed = new Set<string>();
    const waves: TaskPlanItem[][] = [];
    while (pending.length) {
        const ready = pending.filter(({ task }) => (task.depends_on || []).every(id => completed.has(id)));
        if (!ready.length) throw new Error('The approved task plan contains a dependency cycle. No agents were run.');
        for (let index = 0; index < ready.length; index += maxParallel) {
            const wave = ready.slice(index, index + maxParallel);
            waves.push(wave.map(entry => entry.task));
            wave.forEach(entry => completed.add(entry.key));
        }
        const readyKeys = new Set(ready.map(entry => entry.key));
        for (let index = pending.length - 1; index >= 0; index--) {
            if (readyKeys.has(pending[index].key)) pending.splice(index, 1);
        }
    }
    return waves;
}

type FocusMode = 'speed' | 'balanced' | 'quality';
type Phase = 'idle' | 'brainstorming' | 'planning' | 'awaiting_approval' | 'executing' | 'synthesizing';
type DataSource = 'financial' | 'web' | 'docs';

// ─── Constants ───

const AGENT_STYLES: Record<string, { icon: typeof Sparkles; gradient: string; glow: string }> = {
    financial_analyst: { icon: TrendingUp, gradient: 'from-blue-500 to-cyan-400', glow: 'shadow-blue-500/20' },
    legal_advisor: { icon: Scale, gradient: 'from-purple-500 to-violet-400', glow: 'shadow-purple-500/20' },
    risk_assessor: { icon: AlertTriangle, gradient: 'from-amber-500 to-orange-400', glow: 'shadow-amber-500/20' },
    market_researcher: { icon: FileText, gradient: 'from-emerald-500 to-green-400', glow: 'shadow-emerald-500/20' },
    debate_moderator: { icon: Brain, gradient: 'from-pink-500 to-rose-400', glow: 'shadow-pink-500/20' },
    project_manager: { icon: ListChecks, gradient: 'from-cyan-500 to-teal-400', glow: 'shadow-cyan-500/20' },
    scrum_master: { icon: ListChecks, gradient: 'from-cyan-500 to-teal-400', glow: 'shadow-cyan-500/20' },
    valuation_agent: { icon: TrendingUp, gradient: 'from-teal-500 to-emerald-400', glow: 'shadow-teal-500/20' },
    dcf_lbo_architect: { icon: TrendingUp, gradient: 'from-teal-500 to-cyan-400', glow: 'shadow-teal-500/20' },
    due_diligence_agent: { icon: FileText, gradient: 'from-orange-500 to-red-400', glow: 'shadow-orange-500/20' },
    scoring_agent: { icon: CheckCircle2, gradient: 'from-lime-500 to-green-400', glow: 'shadow-lime-500/20' },
    investment_memo_agent: { icon: FileText, gradient: 'from-rose-500 to-pink-400', glow: 'shadow-rose-500/20' },
    compliance_agent: { icon: Scale, gradient: 'from-violet-500 to-indigo-400', glow: 'shadow-violet-500/20' },
    system: { icon: Sparkles, gradient: 'from-indigo-500 to-blue-400', glow: 'shadow-indigo-500/20' },
};

const FOCUS_MODES: { value: FocusMode; label: string; icon: typeof Zap; desc: string; color: string; badge?: string }[] = [
    { value: 'speed', label: 'Speed', icon: Zap, desc: 'Prioritize speed and get the quickest possible answer.', color: 'text-amber-400' },
    { value: 'balanced', label: 'Balanced', icon: Sliders, desc: 'Find the right balance between speed and accuracy.', color: 'text-blue-400' },
    { value: 'quality', label: 'Quality', icon: Star, desc: 'Get the most thorough and accurate answer.', color: 'text-emerald-400', badge: 'Beta' },
];

const DATA_SOURCES: { value: DataSource; label: string; icon: typeof Globe }[] = [
    { value: 'financial', label: 'Financial', icon: TrendingUp },
    { value: 'web', label: 'Web', icon: Globe },
    { value: 'docs', label: 'Docs', icon: BookOpen },
];

const PHASE_LABELS: Record<Phase, { text: string; color: string }> = {
    idle: { text: '', color: '' },
    brainstorming: { text: 'Brainstorming', color: 'text-violet-400' },
    planning: { text: 'Building plan', color: 'text-cyan-400' },
    awaiting_approval: { text: 'Awaiting approval', color: 'text-amber-400' },
    executing: { text: 'Agents running', color: 'text-amber-400' },
    synthesizing: { text: 'Synthesizing', color: 'text-emerald-400' },
};

// ─── Helpers ───

function generateContextAwareFollowUps(completedResults: AgentResult[], taskList: TaskPlanItem[], companyName: string): string[] {
    const sourceOnlyResults = completedResults.length > 0 && completedResults.every(
        result => result?.data?.confidence_basis === 'not_calibrated_source_report'
    );
    if (sourceOnlyResults) {
        return [
            `Show cash flow and balance-sheet metrics with period-specific filing citations for ${companyName}`,
            `Explain the reported revenue growth calculation for ${companyName}`,
        ];
    }

    const suggestions: string[] = [];

    for (let i = 0; i < completedResults.length; i++) {
        const r = completedResults[i];
        const agent = taskList[i]?.assigned_agent || '';
        const reasoning = (r?.reasoning || '').toLowerCase();
        const confidence = r?.confidence ?? 1;

        if (confidence < 0.5) {
            const label = agent.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
            suggestions.push(`Re-run ${label} with additional context for ${companyName}`);
        }
        if (agent.includes('risk') && (reasoning.includes('regulatory') || reasoning.includes('antitrust'))) {
            suggestions.push(`Deep dive into regulatory approval timeline for ${companyName}`);
        }
        if (agent.includes('risk') && reasoning.includes('integration')) {
            suggestions.push('What are the post-merger integration risks and timeline?');
        }
        if (agent.includes('valuation') || agent.includes('dcf')) {
            suggestions.push('Run sensitivity analysis on key DCF assumptions');
        }
        if (agent.includes('financial') && reasoning.includes('synerg')) {
            suggestions.push(`Quantify projected cost and revenue synergies for ${companyName}`);
        }
    }

    suggestions.push('Generate a one-page investment memo');
    suggestions.push(`What comparable transactions support the ${companyName} valuation?`);
    return [...new Set(suggestions)].slice(0, 4);
}

function detectMissingData(dealText: string): string[] {
    const missing: string[] = [];
    const lower = dealText.toLowerCase();
    const factualPublicDataRequest = /\b(fetch|retrieve|report|show|list)\b/.test(lower)
        && /\b(revenue|financial statements?)\b/.test(lower)
        && /\b(sec|10-k|ticker|public company|filing)\b/.test(lower)
        && !/\b(valuation|dcf|lbo|recommendation|investment thesis|risk assessment)\b/.test(lower);
    if (factualPublicDataRequest) return [];

    if (!/\$[\d.,]+[mbk]?\b/i.test(dealText) && !lower.includes('revenue') && !lower.includes('arr'))
        missing.push('Annual revenue or ARR');
    if (!lower.includes('ebitda') && !lower.includes('profit') && !lower.includes('margin'))
        missing.push('EBITDA or profit margins');
    if (!lower.includes('employee') && !lower.includes('team') && !lower.includes('headcount'))
        missing.push('Employee count / team size');
    return missing.slice(0, 3);
}

function getInitials(name: string): string {
    return name
        .split(/[\s_]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map(w => w[0]?.toUpperCase() || '')
        .join('') || '?';
}

interface Citation {
    label: string;
    source: string;
}

// Normalize metadata.citations from the backend (RAG v2 responses) into
// displayable label/source pairs. Accepts strings or {label,source} objects.
function normalizeCitations(raw: unknown): Citation[] {
    if (!raw || !Array.isArray(raw)) return [];
    return (raw as unknown[]).slice(0, 8).map((c, i) => {
        if (typeof c === 'string') {
            const m = c.match(/^(.*?)\s*[›|]\s*(.*)$/);
            return {
                label: m ? m[1].trim() : `Source ${i + 1}`,
                source: m ? m[2].trim() : c.slice(0, 120),
            };
        }
        const entry = c && typeof c === 'object' ? c as Record<string, unknown> : {};
        return {
            label: String(entry.label || entry.filename || entry.title || `Source ${i + 1}`),
            source: String(entry.source || entry.citation || entry.chunk_id || ''),
        };
    }).filter(c => c.label || c.source);
}

function extractCompanyName(text: string): string {
    const tickerMatch = text.match(/\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})\s+\((?:(?:NASDAQ|NYSE|NYSEAMERICAN|AMEX|OTC)\s*:\s*)?[A-Z]{1,5}\)/);
    if (tickerMatch) {
        return tickerMatch[1].replace(/^(?:fetch|analyze|research|assess|review|screen)\s+/i, '').trim();
    }
    const explicit = text.match(/\bfictional\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})(?=\s*(?:[:,.;]|$))/i)
        || text.match(/\b(?:for|of)\s+(?:the\s+)?([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})(?=\s*(?:[:,.;]|$))/i);
    if (explicit) return explicit[1].trim();
    const subject = text.match(/\b(?:fetch|analyze|research|assess|review|screen|value|evaluate)\s+(?:the\s+)?([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5}?)(?=\s+(?:using|with|from|for|based|and)\b|\s*[,;:]|\s*$)/i);
    if (subject) return subject[1].trim();
    const match = text.match(/(?:acquire|acquisition of|merge with|analyze|buy)\s+([A-Z][a-zA-Z\s]+(?:Corp|Inc|LLC|Ltd|Co)?)/i);
    return match ? match[1].trim() : 'Target Company';
}

function formatAgentSummaryLine(agentLabel: string, result: Record<string, unknown>): string {
    const data = result.data && typeof result.data === 'object' ? result.data as Record<string, unknown> : {};
    if (data.confidence_basis === 'not_calibrated_source_report' || data.synthesis_status === 'deterministic_source_report') {
        const sourceLabel = data.data_source === 'sec_edgar_companyfacts' ? 'SEC-sourced facts' : 'Vendor-sourced facts';
        const time = typeof result.execution_time_ms === 'number' ? ` · ${Math.round(result.execution_time_ms as number)}ms` : '';
        const synthesis = data.synthesis_status === 'partial_provider_unavailable'
            ? '; model synthesis incomplete' : '';
        return `**${agentLabel}** — ${sourceLabel}${synthesis}; confidence not calibrated${time}`;
    }
    const calibrated = data.confidence_calibrated === true
        || data.confidence_basis === 'calibrated'
        || data.confidence_basis === 'validated_calibration';
    const confidence = calibrated && typeof result.confidence === 'number'
        ? `${Math.round((result.confidence as number) * 100)}%`
        : 'not calibrated';
    const time = typeof result.execution_time_ms === 'number' ? `${Math.round(result.execution_time_ms as number)}ms` : '';
    return `**${agentLabel}** — ${calibrated ? `Confidence: ${confidence}` : `Agent-reported confidence; not calibrated`}${time ? ` · ${time}` : ''}`;
}

function extractDealScore(results: AgentResult[]): number | null {
    for (const result of results) {
        if (!String(result?._agent_type || '').includes('scoring')) continue;
        const data = result.data || {};
        const raw = Number(data.deal_score ?? data.score ?? data.total_score);
        if (Number.isFinite(raw) && raw >= 0 && raw <= 100) {
            return raw > 1 ? raw / 100 : raw;
        }
    }
    return null;
}

function formatAgentDetailBody(result: Record<string, unknown>): string {
    let output = typeof result.reasoning === 'string' ? (result.reasoning as string) : 'No detailed reasoning available.';

    if (result.data && typeof result.data === 'object') {
        const data = result.data as Record<string, unknown>;
        const historical = Array.isArray(data.historical_financials)
            ? data.historical_financials as Record<string, unknown>[]
            : [];
        const revenueAnalysis = data.revenue_analysis && typeof data.revenue_analysis === 'object'
            ? data.revenue_analysis as Record<string, unknown>
            : {};
        const profitability = data.profitability && typeof data.profitability === 'object'
            ? data.profitability as Record<string, unknown>
            : {};
        const cashFlow = data.cash_flow && typeof data.cash_flow === 'object'
            ? data.cash_flow as Record<string, unknown>
            : {};
        const balanceSheet = data.balance_sheet && typeof data.balance_sheet === 'object'
            ? data.balance_sheet as Record<string, unknown>
            : {};
        const keyFindings = Array.isArray(data.key_findings)
            ? data.key_findings as Record<string, unknown>[]
            : [];

        if (historical.length || Object.keys(revenueAnalysis).length) {
            const amount = (value: unknown) => {
                if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unknown';
                if (Math.abs(value) >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}bn`;
                if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}m`;
                return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
            };
            const pct = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : 'Unknown';
            const isSuppliedInput = (row: Record<string, unknown>) =>
                typeof row.source_type === 'string' && row.source_type.startsWith('user_supplied');
            const suppliedInputs = historical.some(isSuppliedInput);
            const rows = historical.map(row => {
                const year = String(row.fiscal_year || row.period || 'Unknown');
                if (isSuppliedInput(row)) {
                    const provenance = row.source_type === 'user_supplied_shorthand_inferred'
                        ? 'Supplied; metric mapping inferred, verify'
                        : 'Supplied; unverified';
                    return `| ${year} | ${amount(row.revenue)} | ${amount(row.gross_profit)} | ${amount(row.ebitda)} | ${provenance} |`;
                }
                const endDate = String(row.period_end_date || 'Unknown');
                const sourceUrl = typeof row.source_url === 'string' ? row.source_url : '';
                const filing = sourceUrl ? `[${String(row.filing_form || 'SEC filing')}${row.filing_date ? `, filed ${row.filing_date}` : ''}](${sourceUrl})` : 'Source link unavailable';
                return `| ${year} | ${endDate} | ${amount(row.revenue)} | ${pct(row.revenue_yoy_percent)} | ${amount(row.net_income)} | ${pct(row.operating_margin_percent)} | ${filing} |`;
            });
            if (keyFindings.length) {
                const findingSource = suppliedInputs ? 'derived from supplied, unverified inputs' : 'derived from reported financial data';
                output += `\n\n### Key Findings\n\n${keyFindings.map(finding => `- ${String(finding.text || 'Finding not established')} (${findingSource})`).join('\n')}`;
            }
            if (historical.length) {
                output += suppliedInputs
                    ? `\n\n---\n\n### Supplied Financial Inputs (Unverified)\n\n| Fiscal year | Revenue (USD) | Gross profit (USD) | EBITDA (USD) | Provenance |\n| --- | ---: | ---: | ---: | --- |\n${rows.join('\n')}`
                    : `\n\n---\n\n### Reported Financial Data\n\n| Fiscal year | Period end | Revenue (USD) | YoY growth (derived) | Net income (USD) | Operating margin | Filing source |\n| --- | --- | ---: | ---: | ---: | ---: | --- |\n${rows.join('\n')}`;
            } else {
                output += '\n\n---\n\n### Financial Snapshot';
            }
            if (!historical.length && revenueAnalysis.annual_revenue !== undefined) {
                const revenueLabel = revenueAnalysis.growth_basis === 'user supplied' ? 'Latest supplied revenue' : 'Latest reported revenue';
                output += `\n\n${revenueLabel}: ${amount(revenueAnalysis.annual_revenue)}. Growth: ${pct(revenueAnalysis.growth_rate)}.`;
            }
            const valuation = data.valuation && typeof data.valuation === 'object'
                ? data.valuation as Record<string, unknown> : {};
            const valuationEntries = Object.entries(valuation).filter(
                ([key, value]) => /dcf_estimate|multiple_estimate|enterprise_value|equity_value|ev_ebitda_multiple/.test(key)
                    && typeof value === 'number' && Number.isFinite(value)
            );
            const valuationText = valuationEntries.length
                ? valuationEntries.map(([key, value]) => `${key.replace(/_/g, ' ')}: ${key === 'ev_ebitda_multiple' ? `${Number(value).toFixed(2)}x` : amount(value)}`).join('; ')
                : (data.valuation ? 'Not estimated' : 'Unknown');
            output += `\n\nEBITDA: ${amount(profitability.ebitda)}. Valuation: ${valuationText}.`;
            if (profitability.gross_margin !== undefined || profitability.operating_margin !== undefined) {
                output += ` Profitability margins: gross ${pct(profitability.gross_margin)}; operating ${pct(profitability.operating_margin)}.`;
            }
            if (balanceSheet.cash !== undefined || balanceSheet.long_term_debt !== undefined || balanceSheet.debt !== undefined) {
                const debtValue = balanceSheet.long_term_debt ?? balanceSheet.debt;
                output += ` Balance sheet: cash ${amount(balanceSheet.cash)}${balanceSheet.cash_fiscal_year ? ` (${balanceSheet.cash_fiscal_year})` : ''}; debt ${amount(debtValue)}${balanceSheet.debt_fiscal_year ? ` (${balanceSheet.debt_fiscal_year})` : ''}.`;
            }
            if (cashFlow.operating_cash_flow !== undefined) {
                const cashFlowYear = typeof cashFlow.fiscal_year === 'string' ? ` for ${cashFlow.fiscal_year}` : '';
                const capexYear = typeof cashFlow.capital_expenditures_fiscal_year === 'string'
                    ? ` for ${cashFlow.capital_expenditures_fiscal_year}` : '';
                const freeCashFlowYear = typeof cashFlow.free_cash_flow_fiscal_year === 'string'
                    ? ` for ${cashFlow.free_cash_flow_fiscal_year}` : '';
                const sourceLink = (url: unknown) => typeof url === 'string' && url ? ` ([source](${url}))` : '';
                const freeCashFlowSources = Array.isArray(cashFlow.free_cash_flow_source_urls)
                    ? [...new Set(cashFlow.free_cash_flow_source_urls.filter((url): url is string => typeof url === 'string' && Boolean(url)))]
                    : [];
                const freeCashFlowSource = freeCashFlowSources.length
                    ? ` (calculation sources: ${freeCashFlowSources.map(url => `[source](${url})`).join(', ')})` : '';
                output += ` Operating cash flow${cashFlowYear}: ${amount(cashFlow.operating_cash_flow)}${sourceLink(cashFlow.operating_cash_flow_source_url)}; capital expenditures${capexYear}: ${amount(cashFlow.capital_expenditures)}${sourceLink(cashFlow.capital_expenditures_source_url)}; free cash flow${freeCashFlowYear}: ${amount(cashFlow.free_cash_flow)}${freeCashFlowSource}.`;
            }
            if (typeof data.synthesis_status === 'string') {
                output += `\n\nSynthesis status: ${data.synthesis_status.replace(/_/g, ' ')}.`;
            }
            if (typeof data.provider_warning === 'string' && data.provider_warning) {
                output += ` Model note: ${data.provider_warning}`;
            }
            if (Array.isArray(data.data_limitations) && data.data_limitations.length) {
                output += `\n\nData limitations:\n${data.data_limitations.map(item => `- ${String(item)}`).join('\n')}`;
            }
        }

        const longFormKeys = ['memo', 'report', 'markdown', 'content', 'executive_summary'];
        
        for (const key of longFormKeys) {
            if (typeof data[key] === 'string' && (data[key] as string).length > 50) {
                const label = key.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                output += `\n\n---\n\n### 📄 Generated ${label}\n\n${data[key]}`;
            }
        }
    }
    return output;
}

function buildSynthesisMessage(completedResults: AgentResult[], taskList: TaskPlanItem[], companyName: string, failedCount: number): string {
    const metrics: { metric: string; value: string; period: string; source: string }[] = [];
    const risks: string[] = [];
    const findings: string[] = [];
    const limitations = new Set<string>();
    let recommendation = '';
    let dealScore: number | null = null;
    let sourceCount = 0;
    let synthesisIncomplete = false;

    const addMetric = (metric: string, value: unknown, period: unknown, source: string) => {
        if ((typeof value === 'number' && Number.isFinite(value)) || (typeof value === 'string' && value.trim())) {
            metrics.push({ metric, value: String(value), period: typeof period === 'string' ? period : 'Not established', source });
        }
    };

    for (const result of completedResults) {
        const agent = result._agent_type || result.agent || 'unknown';
        const label = agent.replace(/_/g, ' ').replace(/\b\w/g, (char: string) => char.toUpperCase());
        const data = result.data || {};
        const historical = Array.isArray(data.historical_financials)
            ? [...data.historical_financials as Record<string, unknown>[]].sort((a, b) =>
                String(a.period || '').localeCompare(String(b.period || ''), undefined, { numeric: true })) : [];
        const latest = historical.at(-1) || {};
        const revenue = data.revenue_analysis && typeof data.revenue_analysis === 'object'
            ? data.revenue_analysis as Record<string, unknown> : {};
        const profitability = data.profitability && typeof data.profitability === 'object'
            ? data.profitability as Record<string, unknown> : {};
        const cashFlow = data.cash_flow && typeof data.cash_flow === 'object'
            ? data.cash_flow as Record<string, unknown> : {};
        const balance = data.balance_sheet && typeof data.balance_sheet === 'object'
            ? data.balance_sheet as Record<string, unknown> : {};

        if (agent.includes('financial')) {
            addMetric('Revenue', revenue.annual_revenue ?? latest.revenue, latest.fiscal_year || latest.period, label);
            addMetric('Revenue growth', revenue.growth_rate ?? latest.revenue_yoy_percent, latest.fiscal_year || latest.period, label);
            addMetric('Operating margin', profitability.operating_margin ?? latest.operating_margin_percent, latest.fiscal_year || latest.period, label);
            addMetric('Net income', profitability.net_income ?? latest.net_income, latest.fiscal_year || latest.period, label);
            addMetric('Operating cash flow', cashFlow.operating_cash_flow, cashFlow.fiscal_year, label);
            addMetric('Capital expenditures', cashFlow.capital_expenditures, cashFlow.capital_expenditures_fiscal_year, label);
            addMetric('Free cash flow', cashFlow.free_cash_flow, cashFlow.free_cash_flow_fiscal_year, label);
            addMetric('Cash', balance.cash, balance.cash_fiscal_year, label);
            addMetric('Long-term debt', balance.long_term_debt, balance.debt_fiscal_year, label);
            const sourceRows = Array.isArray(data.sources) ? data.sources : [];
            sourceCount += sourceRows.filter(source => source && typeof source === 'object' && 'url' in source).length;
        }

        const riskRows = [data.risks, data.key_risks, data.top_risks].filter(Array.isArray).flat() as unknown[];
        for (const item of riskRows) {
            const text = typeof item === 'string' ? item
                : item && typeof item === 'object'
                    ? String((item as Record<string, unknown>).description || (item as Record<string, unknown>).risk || (item as Record<string, unknown>).text || '')
                    : '';
            if (text && !risks.includes(text)) risks.push(text);
        }
        for (const item of Array.isArray(data.key_findings) ? data.key_findings : []) {
            const text = typeof item === 'string' ? item : item && typeof item === 'object'
                ? String((item as Record<string, unknown>).text || '') : '';
            if (text && !findings.includes(text)) findings.push(text);
        }
        for (const gap of [...(Array.isArray(data.data_limitations) ? data.data_limitations : []), ...(Array.isArray(data.data_gaps) ? data.data_gaps : [])]) {
            if (typeof gap === 'string' && gap.trim()) limitations.add(gap.trim());
        }
        if (data.synthesis_status === 'partial_provider_unavailable' || data.synthesis_status === 'deterministic_source_report') synthesisIncomplete = true;
        if (typeof data.provider_warning === 'string' && data.provider_warning) limitations.add(`Model synthesis unavailable: ${data.provider_warning}`);
        if (typeof data.recommendation === 'string' && data.recommendation.trim()) recommendation ||= data.recommendation;
        if (agent.includes('scoring')) {
            const candidate = Number(data.deal_score ?? data.score ?? data.total_score);
            if (Number.isFinite(candidate) && candidate >= 0 && candidate <= 100) dealScore = candidate > 1 ? candidate : candidate * 100;
        }
    }

    const seenMetrics = new Set<string>();
    const uniqueMetrics = metrics.filter(metric => {
        const key = `${metric.metric}:${metric.period}:${metric.value}`;
        if (seenMetrics.has(key)) return false;
        seenMetrics.add(key);
        return true;
    });
    let md = `## Executive Brief — ${companyName}\n\n`;
    md += `**Review status:** ${failedCount || synthesisIncomplete ? 'Incomplete — human review required' : 'Analysis tasks completed; findings remain subject to source verification'}. `;
    md += `${completedResults.length}/${taskList.length} tasks returned results`;
    if (failedCount) md += `; ${failedCount} failed or were blocked`;
    md += `; ${sourceCount} financial source records captured.\n\n`;
    md += `**Decision:** ${recommendation ? recommendation.slice(0, 500) : 'No supported transaction recommendation recorded.'}`;
    if (dealScore !== null) md += `\n\n**Recorded score:** ${dealScore}/100 (model/agent output; not independently calibrated).`;
    else md += `\n\n**Score:** Not scored.`;

    if (uniqueMetrics.length) {
        md += `\n\n### Evidence Snapshot\n\n| Measure | Recorded value | Period | Producing agent |\n| --- | ---: | --- | --- |\n`;
        for (const metric of uniqueMetrics.slice(0, 12)) md += `| ${metric.metric} | ${metric.value} | ${metric.period} | ${metric.source} |\n`;
    }
    if (findings.length) md += `\n\n### Derived Findings\n\n${findings.slice(0, 4).map(item => `- ${item}`).join('\n')}`;
    if (risks.length) md += `\n\n### Key Risks\n\n${risks.slice(0, 5).map(item => `- ${item}`).join('\n')}`;
    if (limitations.size) md += `\n\n### Diligence Gaps and Caveats\n\n${[...limitations].slice(0, 6).map(item => `- ${item}`).join('\n')}`;
    md += `\n\n*Values and claims above are reproduced from agent outputs; inspect linked evidence in each agent’s detail. Missing values are not zero. Model confidence scores are not calibrated.*`;
    return md;
}

// ─── Animated Dots Component ───

function AnimatedDots() {
    return (
        <span className="inline-flex ml-1">
            <span className="animate-bounce" style={{ animationDelay: '0ms', animationDuration: '1.2s' }}>.</span>
            <span className="animate-bounce" style={{ animationDelay: '200ms', animationDuration: '1.2s' }}>.</span>
            <span className="animate-bounce" style={{ animationDelay: '400ms', animationDuration: '1.2s' }}>.</span>
        </span>
    );
}

// ─── Focus Mode Popover ───

function FocusModePopover({ value, onChange, open, onToggle }: {
    value: FocusMode; onChange: (v: FocusMode) => void; open: boolean; onToggle: () => void;
}) {
    const current = FOCUS_MODES.find(m => m.value === value)!;
    const CurrentIcon = current.icon;

    return (
        <div className="relative">
            <button
                onClick={onToggle}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 
                    bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 ${current.color}`}
            >
                <CurrentIcon className="h-3.5 w-3.5" />
                <span>{current.label}</span>
                <ChevronDown className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute bottom-full left-0 mb-2 w-72 rounded-xl border border-white/10 bg-[#1a1a2e]/95 backdrop-blur-xl shadow-2xl shadow-black/50 z-50 overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-200">
                    {FOCUS_MODES.map((mode) => {
                        const Icon = mode.icon;
                        const isActive = mode.value === value;
                        return (
                            <button
                                key={mode.value}
                                onClick={() => { onChange(mode.value); onToggle(); }}
                                className={`w-full flex items-start gap-3 px-4 py-3 text-left transition-all duration-150 
                                    ${isActive ? 'bg-white/10' : 'hover:bg-white/5'}`}
                            >
                                <Icon className={`h-4 w-4 mt-0.5 ${mode.color}`} />
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className={`text-sm font-semibold ${isActive ? 'text-white' : 'text-white/80'}`}>{mode.label}</span>
                                        {mode.badge && (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-medium">{mode.badge}</span>
                                        )}
                                    </div>
                                    <span className="text-xs text-white/50 leading-tight">{mode.desc}</span>
                                </div>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ─── Source Toggles ───

function SourceToggles({ active, onToggle }: { active: DataSource[]; onToggle: (s: DataSource) => void }) {
    const [open, setOpen] = useState(false);

    return (
        <div className="relative">
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 text-blue-400"
            >
                <Database className="h-3.5 w-3.5" />
                <span>Sources</span>
                <span className="text-[10px] bg-blue-500/20 text-blue-300 rounded-full px-1.5">{active.length}</span>
            </button>

            {open && (
                <div className="absolute bottom-full left-0 mb-2 w-52 rounded-xl border border-white/10 bg-[#1a1a2e]/95 backdrop-blur-xl shadow-2xl shadow-black/50 z-50 overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-200">
                    {DATA_SOURCES.map((src) => {
                        const Icon = src.icon;
                        const isActive = active.includes(src.value);
                        return (
                            <button
                                key={src.value}
                                onClick={() => onToggle(src.value)}
                                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/5 transition-colors"
                            >
                                <div className="flex items-center gap-2.5">
                                    <Icon className={`h-4 w-4 ${isActive ? 'text-blue-400' : 'text-white/40'}`} />
                                    <span className={`text-sm ${isActive ? 'text-white' : 'text-white/50'}`}>{src.label}</span>
                                </div>
                                <div className={`w-8 h-4.5 rounded-full transition-colors duration-200 flex items-center px-0.5 
                                    ${isActive ? 'bg-blue-500' : 'bg-white/10'}`}>
                                    <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform duration-200 
                                        ${isActive ? 'translate-x-3.5' : 'translate-x-0'}`} />
                                </div>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ═══════════════════════════════════════
//  MAIN COMPONENT
// ═══════════════════════════════════════

export function ChatWindow() {
    const store = useDealForgeStore();
    const activeConv = store.getActiveConversation();

    // Hydrate from Redis on mount (primary persistence)
    useEffect(() => {
        void useDealForgeStore.getState().loadFromBackend();
    }, []);

    useEffect(() => {
        if (!useDealForgeStore.getState().getActiveConversation()) {
            const currentStore = useDealForgeStore.getState();
            const convId = currentStore.createConversation();
            currentStore.addMessage(convId, {
                role: 'system',
                content: 'Welcome to **DealForge AI**. Describe a deal and our multi-agent team will analyze it end-to-end.\n\nTry: *"Analyze the acquisition of Stripe, a SaaS company with $50M ARR"*',
                agentName: 'DealForge AI',
            });
        }
    }, [activeConv?.id]);

    const messages: Message[] = (activeConv?.messages || []).map(m => ({
        id: m.id, role: m.role, content: m.content, agentName: m.agentName,
        timestamp: new Date(m.timestamp), status: m.status, provider: m.provider,
        followUps: m.followUps, missingData: m.missingData, metadata: m.metadata,
    }));
    const lastUserMessageIndex = messages.reduce((last, message, index) => message.role === 'user' ? index : last, -1);
    const currentTurnAgentError = messages.some((message, index) =>
        index > lastUserMessageIndex && message.role === 'agent' && message.status === 'error'
    );

    // ─── State ───
    const [input, setInput] = useState('');
    const [phase, setPhase] = useState<Phase>('idle');
    const [activeDealId, setActiveDealId] = useState<string | null>(activeConv?.dealId || null);
    const [dealCompleted, setDealCompleted] = useState(false);
    const completedResultsRef = useRef<AgentResult[]>([]);
    const taskListRef = useRef<TaskPlanItem[]>([]);
    const [collapsedAgents, setCollapsedAgents] = useState<Record<string, boolean>>({});
    const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
    const [followUps, setFollowUps] = useState<string[]>([]);
    const [copiedId, setCopiedId] = useState<string | null>(null);
    const [ratedIds, setRatedIds] = useState<Record<number, 'up' | 'down'>>({});
    const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');
    const [focusMode, setFocusMode] = useState<FocusMode>('balanced');
    const [activeSources, setActiveSources] = useState<DataSource[]>(['financial', 'docs']);
    const [focusOpen, setFocusOpen] = useState(false);
    const [executingProgress, setExecutingProgress] = useState({ done: 0, total: 0 });
    const [approvalRequest, setApprovalRequest] = useState<{
        taskCount: number;
        label?: string;
        resolve: (approved: boolean) => void;
    } | null>(null);
    // The approve button only arms ~900ms after a request appears, so a stray
    // click on the previous prompt cannot approve it. Arming is tied to the
    // request object itself, so a new request is never armed by an old timer.
    const [armedRequest, setArmedRequest] = useState<typeof approvalRequest>(null);
    const approvalArmed = approvalRequest !== null && armedRequest === approvalRequest;
    const eventSourceRef = useRef<EventSource | null>(null);

    useEffect(() => {
        if (!approvalRequest) return;
        const timer = window.setTimeout(() => setArmedRequest(approvalRequest), 900);
        return () => window.clearTimeout(timer);
    }, [approvalRequest]);

    // Use ref to avoid stale closure issues
    const addMessageRef = useRef<(msg: Omit<Message, 'id' | 'timestamp'>) => void>(() => {});

    // SSE streaming connection (with exponential-backoff reconnect)
    const sseRetryRef = useRef(0);
    const sseManualCloseRef = useRef(false);
    const sseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const SSE_MAX_RETRIES = 5;

    const connectSSE = useCallback((dealId: string) => {
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
        }
        if (sseTimerRef.current) {
            clearTimeout(sseTimerRef.current);
            sseTimerRef.current = null;
        }
        sseManualCloseRef.current = false;
        sseRetryRef.current = 0;

        const openStream = () => {
            const eventSource = new EventSource(`${API_BASE}/api/v1/stream/events/${dealId}`);
            eventSourceRef.current = eventSource;

            eventSource.addEventListener('connected', () => {
                sseRetryRef.current = 0; // reset backoff on healthy connect
            });

        eventSource.addEventListener('agent_starting', (e) => {
            const data = JSON.parse(e.data);
            addMessageRef.current({
                role: 'agent',
                agentName: data.agent_type.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase()),
                content: `▶ **${data.task_title}** — Starting...`,
                status: 'thinking',
            });
        });

        eventSource.addEventListener('agent_progress', (e) => {
            const data = JSON.parse(e.data);
            setExecutingProgress(prev => ({ ...prev, progress: data.progress || 0 }));
        });

        eventSource.addEventListener('agent_completed', (e) => {
            const data = JSON.parse(e.data);
            setExecutingProgress(prev => ({ done: prev.done + 1, total: prev.total }));
            const agentLabel = (data.agent_type || '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
            addMessageRef.current({
                role: 'agent',
                agentName: agentLabel,
                content: data.reasoning || `Analysis complete.`,
                status: 'done',
                provider: data.provider_used,
                metadata: {
                    confidence: data.confidence,
                    execution_time_ms: data.execution_time_ms,
                    result: data.result,
                },
            });
        });

        eventSource.addEventListener('agent_error', (e) => {
            const data = JSON.parse(e.data);
            addMessageRef.current({
                role: 'system',
                content: `⚠️ **${data.agent_type}** failed: ${data.error}`,
                status: 'error',
            });
        });

        eventSource.addEventListener('phase_changed', (e) => {
            const data = JSON.parse(e.data);
            setPhase(data.phase as Phase);
        });

        eventSource.addEventListener('deal_complete', (e) => {
            const data = JSON.parse(e.data);
            const scoreLine = data.final_score == null
                ? 'Deal score: Not scored'
                : 'Score: ' + data.final_score + '/100';
            addMessageRef.current({
                role: 'system',
                content: '🎉 **Deal Analysis Complete**\n\n' + scoreLine + '\n' + data.recommendation,
            });
            setPhase('idle');
        });

        eventSource.addEventListener('deal_needs_review', (e) => {
            const data = JSON.parse(e.data);
            addMessageRef.current({
                role: 'system',
                content: `⚠️ **Analysis needs review**\n\n${data.recommendation || 'One or more tasks did not complete successfully.'}`,
                status: 'error',
            });
            setPhase('idle');
        });

            eventSource.onerror = () => {
                eventSource.close();
                if (eventSourceRef.current === eventSource) {
                    eventSourceRef.current = null;
                }
                if (sseManualCloseRef.current || sseRetryRef.current >= SSE_MAX_RETRIES) {
                    if (sseRetryRef.current >= SSE_MAX_RETRIES) {
                        console.error('SSE reconnect exhausted after', SSE_MAX_RETRIES, 'attempts');
                    }
                    return;
                }
                // Exponential backoff: 1s, 2s, 4s, 8s, 8s…
                const delay = Math.min(1000 * 2 ** sseRetryRef.current, 8000);
                sseRetryRef.current += 1;
                sseTimerRef.current = setTimeout(() => {
                    if (!sseManualCloseRef.current) openStream();
                }, delay);
            };

            return eventSource;
        };

        return openStream();
    }, []);

    const disconnectSSE = useCallback(() => {
        sseManualCloseRef.current = true;
        if (sseTimerRef.current) {
            clearTimeout(sseTimerRef.current);
            sseTimerRef.current = null;
        }
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
        }
    }, []);

    useEffect(() => {
        const currentConversation = useDealForgeStore.getState().getActiveConversation();
        const dealId = currentConversation?.dealId || null;
        const completed = Boolean(
            dealId && currentConversation?.messages.some(
                message => message.agentName === 'DealForge Summary'
            )
        );

        // Switching conversations resets all per-conversation state at once.
        /* eslint-disable react-hooks/set-state-in-effect */
        setActiveDealId(dealId);
        setDealCompleted(completed);
        completedResultsRef.current = [];
        taskListRef.current = [];
        setFollowUps([]);
        /* eslint-enable react-hooks/set-state-in-effect */
        disconnectSSE();
    }, [activeConv?.id, disconnectSSE]);

    // Disconnect SSE on unmount. Deal switches are covered because
    // connectSSE() always closes the previous stream before opening a new one.
    useEffect(() => disconnectSSE, [disconnectSSE]);

    const isProcessing = phase !== 'idle';

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Follow new content only while the reader is at the bottom; otherwise show a
    // "Jump to latest" button instead of yanking them away from what they're reading.
    const [atBottom, setAtBottom] = useState(true);
    const atBottomRef = useRef(true);
    const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    useEffect(() => {
        const el = messagesEndRef.current;
        if (!el || typeof IntersectionObserver === 'undefined') return;
        const observer = new IntersectionObserver(([entry]) => {
            atBottomRef.current = entry.isIntersecting;
            setAtBottom(entry.isIntersecting);
        }, { threshold: 0, rootMargin: '0px 0px 80px 0px' });
        observer.observe(el);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        const last = messages[messages.length - 1];
        if (atBottomRef.current || last?.role === 'user') scrollToBottom();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [messages.length, followUps.length, phase]);

    const autoResize = useCallback(() => {
        const ta = textareaRef.current;
        if (ta) { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 150)}px`; }
    }, []);
    useEffect(() => { autoResize(); }, [input, autoResize]);

    const convId = activeConv?.id || '';

    function addMessage(msg: Omit<Message, 'id' | 'timestamp'>) {
        if (!convId) return '';
        const msgId = store.addMessage(convId, {
            role: msg.role, content: msg.content, agentName: msg.agentName,
            status: msg.status, provider: msg.provider,
        });
        if (msg.role === 'user' && messages.filter(m => m.role === 'user').length === 0) {
            store.updateConversationTitle(convId, msg.content.slice(0, 50) + (msg.content.length > 50 ? '...' : ''));
        }
        return msgId;
    }

    useEffect(() => {
        addMessageRef.current = addMessage;
    });

    function updateMessage(id: string, updates: Partial<Message>) {
        if (!convId) return;
        store.updateMessage(convId, id, {
            content: updates.content, status: updates.status,
            agentName: updates.agentName, provider: updates.provider,
            followUps: updates.followUps, missingData: updates.missingData,
            metadata: updates.metadata,
        });
    }

    async function uploadFiles(dealId: string) {
        for (const file of uploadedFiles) {
            const formData = new FormData();
            formData.append('file', file);
            try {
                await fetch(`${API_BASE}/api/v1/documents/upload?deal_id=${dealId}`, { method: 'POST', body: formData });
                addMessage({ role: 'agent', agentName: 'PageIndex', content: `📄 Indexed **${file.name}** into knowledge base.` });
            } catch {
                addMessage({ role: 'agent', agentName: 'PageIndex', content: `⚠️ Failed to index ${file.name}`, status: 'error' });
            }
        }
        setUploadedFiles([]);
    }

    function toggleSource(s: DataSource) {
        setActiveSources(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
    }

    // ─── Main send handler ───

    async function handleSend(overrideText?: string, isRegenerate = false) {
        let userText = (overrideText || input).trim();
        if (!userText && uploadedFiles.length === 0) return;

        setInput('');
        setFollowUps([]);
        setCollapsedAgents({});
        setPhase('brainstorming');

        const lastAgentMsg = messages.slice().reverse().find(m => m.role === 'agent' || m.role === 'assistant');
        const isAnsweringQuestions = lastAgentMsg?.metadata?.pending_clarification;

        if (!isRegenerate) {
            addMessage({ role: 'user', content: userText });
        } else {
            userText = overrideText || '';
        }

        // Missing data hint
        const missing = detectMissingData(userText);
        if (missing.length > 0 && !activeDealId && !isAnsweringQuestions) {
            addMessage({
                role: 'system', agentName: 'Data Assistant',
                content: `💡 For a more accurate analysis, consider providing:\n\n${missing.map(m => `• ${m}`).join('\n')}\n\nYou can add these details in your next message, or I'll proceed with what's available.`,
                missingData: missing,
            });
        }

        try {
            let dealId = activeDealId;
            let currentPrompt = userText;
            let userAnswers: ClarificationAnswer[] = [];
            let currentRound = 0;

            if (isAnsweringQuestions) {
                const isSkipAll = userText.toLowerCase() === 'skip' || userText.toLowerCase() === 'skip all';
                const isAskMore = userText.toLowerCase() === 'more' || userText.toLowerCase() === 'ask more';
                
                updateMessage(lastAgentMsg!.id, { metadata: { ...lastAgentMsg!.metadata, pending_clarification: false } });
                dealId = lastAgentMsg!.metadata!.deal_id as string;
                currentPrompt = lastAgentMsg!.metadata!.original_prompt as string;
                currentRound = Number(lastAgentMsg!.metadata!.clarification_round || 0);
                
                if (isAskMore) {
                    // rounds handled by backend
                } else if (!isSkipAll) {
                    userAnswers = [{ question: "User Response", answer: userText }];
                    currentRound += 1;
                }
                
                setActiveDealId(dealId);

                if (!isSkipAll && !isAskMore) {
                    const detectedDealType = userText.toLowerCase().includes('lbo') ? 'lbo'
                        : userText.toLowerCase().includes('ipo') ? 'ipo'
                            : (currentPrompt || '').toLowerCase().includes('acqui') ? 'm_a' : 'valuation';
                    fetch(`${API_BASE}/api/v1/chat/clarify/feedback`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            deal_type: detectedDealType,
                            questions: lastAgentMsg!.metadata!.questions || [],
                            user_answer: userText,
                            task_score: 0.75,
                        }),
                    }).catch(() => { });
                }
            } else if (lastAgentMsg?.metadata?.is_assumptions_summary) {
                updateMessage(lastAgentMsg!.id, { metadata: { ...lastAgentMsg!.metadata, is_assumptions_summary: false } });
                dealId = lastAgentMsg!.metadata!.deal_id as string;
                currentPrompt = lastAgentMsg!.metadata!.original_prompt as string;
                
                setActiveDealId(dealId);
                await runPlanningPhase(currentPrompt, dealId!, [], 3, userText);
                return;
            } else if (dealCompleted && activeDealId) {
                dealId = activeDealId;
                setDealCompleted(false);

                addMessage({
                    role: 'agent', agentName: 'Scrum Master',
                    content: `🔄 **Follow-up on existing deal** — Reusing context from ${completedResultsRef.current.length} prior agent results.`,
                    status: 'done',
                });
            } else if (!isDealAnalysisRequest(userText, uploadedFiles.length > 0, Boolean(activeDealId))) {
                const responseId = addMessage({
                    role: 'assistant', agentName: 'DealForge AI',
                    content: 'Thinking...', status: 'thinking',
                });
                try {
                    const response = await fetch(`${API_BASE}/api/v1/chat/respond`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            prompt: userText,
                            local_only: /\b(?:local[- ]only|lm\s*studio\s+only|no\s+(?:cloud|remote)\s+(?:llm|models?))\b/i.test(userText),
                        }),
                    });
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail || `Chat response failed (HTTP ${response.status})`);
                    updateMessage(responseId, {
                        content: result.response,
                        status: 'done',
                        provider: result.provider,
                        metadata: { model: result.model, route_tier: result.route_tier },
                    });
                } catch (error) {
                    const detail = error instanceof Error ? error.message : 'Direct chat request failed';
                    updateMessage(responseId, { content: `⚠️ ${detail}`, status: 'error' });
                    setPhase('idle');
                    return;
                }
                setPhase('idle');
                return;
            } else {
                const createRes = await fetch(`${API_BASE}/api/v1/deals`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: userText.substring(0, 80), target_company: extractCompanyName(userText),
                        description: userText, industry: 'technology',
                        context: { user_prompt: userText, focus_mode: focusMode, sources: activeSources },
                    }),
                });
                if (!createRes.ok) throw new Error('Failed to create deal');
                const deal = await createRes.json();
                dealId = deal.id;
                setActiveDealId(dealId);
                store.setConversationDealId(convId, dealId!);
                completedResultsRef.current = [];
                taskListRef.current = [];

                if (uploadedFiles.length > 0) await uploadFiles(dealId!);
                connectSSE(dealId!);

                const thinkingId = addMessage({
                    role: 'agent', agentName: 'Scrum Master',
                    content: '🧠 **Analyzing your request...**\n\n> Checking data requirements and identifying potential risks...',
                    status: 'thinking',
                });

                const clarifyRes = await fetch(`${API_BASE}/api/v1/chat/clarify`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: currentPrompt,
                        deal_id: dealId,
                        company_name: extractCompanyName(currentPrompt),
                        clarification_round: currentRound,
                        user_skipped: userText.toLowerCase() === 'skip' || userText.toLowerCase() === 'skip all',
                        skipped_questions: (userText.toLowerCase() === 'skip' || userText.toLowerCase() === 'skip all') 
                            ? (lastAgentMsg?.metadata?.questions || []) : []
                    }),
                });

                if (clarifyRes.ok) {
                    const clarifyData = await clarifyRes.json();
                    
                    if (clarifyData.assumptions_summary?.confirmation_required) {
                        updateMessage(thinkingId, {
                            content: clarifyData.assumptions_summary.formatted_message,
                            status: 'done',
                            metadata: {
                                is_assumptions_summary: true,
                                assumptions: clarifyData.assumptions_summary,
                                deal_id: dealId ?? undefined,
                                original_prompt: currentPrompt
                            }
                        });
                        setPhase('idle');
                        return;
                    }

                    if (clarifyData.clarifying_questions?.length > 0) {
                        const questions = clarifyData.clarifying_questions as ClarificationQuestion[];
                        const round = clarifyData.qa_controls?.current_round || (currentRound + 1);
                        const maxR = clarifyData.qa_controls?.max_rounds || 3;
                        
                        const questionList = questions.map((q, i) =>
                            `**Q${i + 1}:** ${q.question}\n*Reasoning: ${q.reasoning}*`).join('\n\n');
                        updateMessage(thinkingId, {
                            content: `🧠 **Scrum Master — Clarification Round ${round} of ${maxR}**\n\n${questionList}\n\n---\n*Reply to proceed, or use the controls below.*`,
                            status: 'done',
                            metadata: {
                                pending_clarification: true,
                                original_prompt: currentPrompt,
                                deal_id: dealId ?? undefined,
                                clarification_round: round,
                                questions: questions,
                                qa_controls: clarifyData.qa_controls
                            }
                        });
                        setPhase('idle');
                        return;
                    } else {
                        updateMessage(thinkingId, { content: '🧠 **Scrum Master** — Request is clear, proceeding to build plan.', status: 'done' });
                    }
                } else {
                    updateMessage(thinkingId, { content: '⚠️ Skipping clarification — AI Service unavailable. Proceeding.', status: 'done' });
                }
            }

            await runPlanningPhase(currentPrompt, dealId!, userAnswers, currentRound, userText);
        } catch (err) {
            addMessage({
                role: 'system',
                content: `❌ Error: ${err instanceof Error ? err.message : 'Connection failed'}. Make sure the backend is running.`,
                status: 'error',
            });
        }
        setPhase('idle');
    }

    async function runPlanningPhase(currentPrompt: string, dealId: string, userAnswers: ClarificationAnswer[], _currentRound: number, userText: string) {
        setPhase('planning');
        const thinkingPlanId = addMessage({
            role: 'agent', agentName: 'Scrum Master',
            content: '📋 **Building Plan...**\n\n> Reasoning about the best approach and planning the task pipeline...',
            status: 'thinking',
        });

        try {
            const taskListsUrl = `${API_BASE}/api/v1/deals/${dealId}/tasks`;
            const knownTaskListIds = new Set<string>();
            let taskListSnapshotTaken = false;
            try {
                const beforeRes = await fetch(taskListsUrl);
                if (beforeRes.ok) {
                    const before = await beforeRes.json() as { todo_lists?: TaskListRecord[] };
                    for (const list of before.todo_lists || []) knownTaskListIds.add(list.id);
                    taskListSnapshotTaken = true;
                }
            } catch {
                // The plan request remains authoritative; this snapshot only helps recover lost responses.
            }

            // Runs inside an async event handler, not during render.
            // eslint-disable-next-line react-hooks/purity
            const planRequestStartedAt = Date.now();
            let planResponse: PlanResponse | null = null;
            let planRes: Response | null = null;
            let planRequestError: unknown = null;
            let planRecovered = false;
            try {
                planRes = await fetch(`${API_BASE}/api/v1/chat/plan`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: currentPrompt, deal_id: dealId,
                        company_name: extractCompanyName(currentPrompt),
                        user_answers: userAnswers,
                        focus_mode: focusMode, sources: activeSources,
                        local_only: /\b(?:local[- ]only|lm\s*studio\s+only|no\s+(?:cloud|remote)\s+(?:llm|models?))\b/i.test(currentPrompt),
                        force_analysis: /\b(?:re-?run|fresh\s+analysis|re-?analy[sz]e|from\s+scratch)\b/i.test(currentPrompt),
                    }),
                });
                if (planRes.ok) planResponse = await planRes.json() as PlanResponse;
            } catch (error) {
                planRequestError = error;
            }

            if (!planResponse && planRequestError) {
                try {
                    const recoveryRes = await fetch(taskListsUrl);
                    if (recoveryRes.ok) {
                        const recovery = await recoveryRes.json() as { todo_lists?: TaskListRecord[] };
                        const recoveredPlan = (recovery.todo_lists || [])
                            .filter(list => taskListSnapshotTaken
                                ? !knownTaskListIds.has(list.id)
                                : Date.parse(list.created_at || '') >= planRequestStartedAt - 5000)
                            .filter(list => (list.items?.length || 0) > 0)
                            .sort((a, b) => Date.parse(b.created_at || '') - Date.parse(a.created_at || ''))[0];
                        if (recoveredPlan) {
                            planRecovered = true;
                            planResponse = {
                                reasoning: 'The task plan was recovered from the backend after its response was interrupted.',
                                data: { todo_list: recoveredPlan },
                            };
                        }
                    }
                } catch {
                    // Preserve the original planning error if recovery is unavailable.
                }
            }

            if (!planResponse && !planRes) throw planRequestError;

            // Deliverable request on an already-analysed deal: build it from the
            // saved results instead of re-running the agents.
            const documentPlan = planRes?.ok ? planResponse?.data?.document_plan : undefined;
            if (documentPlan) {
                const sectionList = documentPlan.sections.map((section, i) => `${i + 1}. ${section.title}`).join('\n');
                const notes = [...documentPlan.assumptions, ...documentPlan.questions].map(note => `- ${note}`).join('\n');
                const gapLine = documentPlan.gaps.length
                    ? `⚠️ **Missing evidence:** ${documentPlan.gaps.join(', ')}${documentPlan.gap_agents.length ? ` (owned by ${documentPlan.gap_agents.join(', ')})` : ''}\n\n`
                    : '';
                const documentPlanMessage = `📄 **Document Plan — ${documentPlan.title}**\n\n` +
                    `> **💭 Reasoning:** ${planResponse?.reasoning || 'Built from the saved analysis.'}\n\n` +
                    `**Formats:** ${documentPlan.formats.map(f => f.toUpperCase()).join(', ')} · **Audience:** ${documentPlan.audience}\n\n` +
                    `${sectionList}\n\n${gapLine}${notes ? `${notes}\n\n` : ''}` +
                    `_Built from the saved analysis. To re-run the agents first, ask again with "fresh analysis"._\n\n` +
                    `---\nAwaiting your approval to generate.`;
                updateMessage(thinkingPlanId, { content: documentPlanMessage, status: 'done' });
                setPhase('awaiting_approval');
                const approvedDocument = await new Promise<boolean>(resolve => {
                    setApprovalRequest({
                        taskCount: documentPlan.sections.length,
                        label: `Review the ${documentPlan.title} plan above. Nothing is generated until you approve.`,
                        resolve,
                    });
                });
                setApprovalRequest(null);
                if (!approvedDocument) {
                    addMessage({ role: 'system', content: 'Document plan saved. Nothing was generated.' });
                    setPhase('idle');
                    return;
                }
                updateMessage(thinkingPlanId, {
                    content: documentPlanMessage.replace('Awaiting your approval to generate.', 'Approved. Generating from saved results.'),
                });
                const genRes = await fetch(`${API_BASE}/api/v1/deals/${dealId}/documents/generate`, withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(planResponse?.data?.document_request ?? { request: currentPrompt }),
                }));
                const generated = await genRes.json() as {
                    formats_generated?: string[];
                    errors?: { format: string; error: string }[];
                    review_actions?: string[];
                    detail?: string;
                };
                if (!genRes.ok) throw new Error(generated.detail || `Document generation failed (HTTP ${genRes.status}).`);
                const ready = (generated.formats_generated || []).map(f => f.toUpperCase());
                const failures = (generated.errors || []).map(item => `${item.format.toUpperCase()}: ${item.error}`).join('; ');
                const actions = (generated.review_actions || []).map(action => `- ${action}`).join('\n');
                addMessage({
                    role: 'assistant',
                    content: `${ready.length ? `✅ **${documentPlan.title}** generated: ${ready.join(', ')}.` : `⚠️ **${documentPlan.title}** was not generated.`}` +
                        `${failures ? `\n\nErrors: ${failures}` : ''}` +
                        `\n\nOpen the Reports Hub to review and approve before download.${actions ? `\n\n${actions}` : ''}`,
                });
                setPhase('idle');
                return;
            }

            let taskList: TaskPlanItem[] = [];
            let taskListId: string | null = null;
            let planMessageContent = '';
            if (planResponse && (planRes?.ok || planRecovered)) {
                const plan = planResponse;
                taskList = plan.data?.todo_list?.items || [];
                taskListId = plan.data?.todo_list?.id || null;
                const reasoning = plan.reasoning || 'Generated task pipeline.';

                const agentCount = new Set(taskList.map(t => t.assigned_agent)).size;
                const taskListMd = taskList.map((t, i) =>
                    `${i + 1}. **${t.title}** → \`${(t.assigned_agent || '').replace(/_/g, ' ')}\` *(${t.priority})*\n   ${t.description}`
                ).join('\n');
                const selectedAgent = plan.data?.laya_decision?.selected_agent;

                planMessageContent = `🧠 **Scrum Master — Task Plan ${planRecovered ? 'Recovered' : 'Created'}**\n\n` +
                    `> **💭 Reasoning:** ${reasoning.split('\n').join('\n> ')}\n\n` +
                    `${selectedAgent ? `Laya routed this scope to **${selectedAgent.replace(/_/g, ' ')}**.\n\n` : ''}` +
                    `📋 **${taskList.length} tasks** assigned to **${agentCount} agents**:\n\n${taskListMd}\n\n---\nAwaiting your approval to run.`;
                updateMessage(thinkingPlanId, { content: planMessageContent, status: 'done' });
            } else {
                taskList = [
                    { title: 'Financial Analysis', description: `Perform financial analysis for: ${userText}`, assigned_agent: 'financial_analyst', priority: 'critical' },
                    { title: 'Market Research', description: `Research the market for: ${userText}`, assigned_agent: 'market_researcher', priority: 'high' },
                    { title: 'Legal Review', description: `Perform legal due diligence for: ${userText}`, assigned_agent: 'legal_advisor', priority: 'high' },
                    { title: 'Risk Assessment', description: `Assess risks for: ${userText}`, assigned_agent: 'risk_assessor', priority: 'high' },
                ];
                const fallbackItems = taskList.map((item, i) => `${i + 1}. **${item.title}** → \`${item.assigned_agent.replace(/_/g, ' ')}\` *(${item.priority})*\n   ${item.description}`).join('\n');
                planMessageContent = `🧠 **Scrum Master** — Backend plan unavailable. Review this fallback plan before execution.\n\n📋 **${taskList.length} tasks**:\n\n${fallbackItems}\n\n---\nAwaiting your approval to run.`;
                updateMessage(thinkingPlanId, { content: planMessageContent, status: 'done' });
            }

            if (!taskList.length) {
                throw new Error('The planner returned no executable tasks. No work was started.');
            }
            const executionWaves = buildExecutionWaves(taskList);

            setPhase('awaiting_approval');
            const approved = await new Promise<boolean>(resolve => {
                setApprovalRequest({ taskCount: taskList.length, resolve });
            });
            setApprovalRequest(null);
            if (!approved) {
                addMessage({ role: 'system', content: 'Plan saved. Execution was cancelled; no agents were run.' });
                setPhase('idle');
                return;
            }
            if (taskListId) {
                const approvalRes = await fetch(`${API_BASE}/api/v1/tasks/${taskListId}/approve`, { method: 'POST' });
                if (!approvalRes.ok) throw new Error(`Plan approval failed (HTTP ${approvalRes.status}). No tasks were run.`);
            }
            updateMessage(thinkingPlanId, {
                content: planMessageContent.replace('Awaiting your approval to run.', 'Approved. Execution started.'),
            });

            // ─── Execute Tasks ───
            setPhase('executing');
            const completedResults: AgentResult[] = [];
            let completedCount = 0;
            let failedCount = 0;
            let attemptedCount = 0;
            setExecutingProgress({ done: 0, total: taskList.length });

            // Emit SSE: phase changed to planning
            fetch(
                `${API_BASE}/api/v1/stream/emit/${dealId}`,
                withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ event_type: 'phase_changed', data: { phase: 'planning' } }),
                })
            ).catch(() => { });

            // Mark the deal as running on the Dashboard
            fetch(`${API_BASE}/api/v1/deals/${dealId}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'running', current_stage: 'analysis' }),
            }).catch(() => { });
            if (taskListId) {
                const statusRes = await fetch(`${API_BASE}/api/v1/tasks/${taskListId}/status`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: 'in_progress' }),
                });
                if (!statusRes.ok) throw new Error(`Could not start approved task list (HTTP ${statusRes.status}).`);
            }

            const progressId = addMessage({
                role: 'system', agentName: 'Progress',
                content: `📊 **Progress:** 0/${taskList.length} agents complete`,
            });

            const taskOutcomes = new Map<string, 'done' | 'blocked'>();
            for (const taskWave of executionWaves) {
                const priorOutputs: Record<string, unknown> = {};
                for (const result of completedResults) {
                    if (result._agent_type && result.data) priorOutputs[result._agent_type] = result.data;
                }
                await Promise.all(taskWave.map(async task => {
                let taskStatus: 'done' | 'blocked' = 'blocked';
                let taskResult: Record<string, unknown> = {};
                const agentLabel = (task.assigned_agent || 'analyst').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
                const taskMsgId = addMessage({
                    role: 'agent', agentName: agentLabel,
                    content: `⏳ **${task.title}** — Analyzing...`, status: 'thinking',
                });
                const unmetDependencies = (task.depends_on || []).filter(id => taskOutcomes.get(id) !== 'done');
                if (unmetDependencies.length) {
                    failedCount++;
                    taskResult = { error: 'blocked_by_failed_dependencies', blocked_by: unmetDependencies };
                    updateMessage(taskMsgId, {
                        content: `⚠️ **${task.title}** — Blocked because prerequisite task(s) did not complete: ${unmetDependencies.join(', ')}.`,
                        status: 'error',
                    });
                } else {
                  try {
                    await new Promise(resolve => setTimeout(resolve, 500));
                    const taskRes = await fetch(`${API_BASE}/api/v1/chat/execute-task`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            agent_type: task.assigned_agent, task: task.description,
                            user_prompt: userText,
                            local_only: /\b(?:local[- ]only|lm\s*studio\s+only|no\s+(?:cloud|remote)\s+(?:llm|models?))\b/i.test(userText),
                            deal_id: dealId, task_id: task.id || `task-${completedCount}`,
                            task_list_id: taskListId,
                            title: task.title, sources: activeSources,
                            company_name: extractCompanyName(userText), // Added company_name propagation
                            agent_outputs: priorOutputs, // Injected prior contextual data
                        }),
                    });

                    if (taskRes.ok) {
                        const result = await taskRes.json() as AgentResult;
                        if (result.success === false || result.error) {
                            throw new Error(result.reasoning || result.error || 'Agent reported failure');
                        }
                        result._agent_type = task.assigned_agent;
                        const summaryLine = formatAgentSummaryLine(agentLabel, result);
                        const detailBody = formatAgentDetailBody(result);
                        completedResults.push(result);
                        completedCount++;
                        taskStatus = 'done';
                        taskResult = result.data || {};

                        setCollapsedAgents(prev => ({ ...prev, [taskMsgId]: true }));
                        updateMessage(taskMsgId, {
                            content: `${summaryLine}\n\n---\n\n${detailBody}`, status: 'done',
                            provider: result.provider || 'unknown',
                            metadata: { ...result.data, _agentSummary: summaryLine, _agentDetail: detailBody, action_id: result.action_id },
                        });

                        // Forward real agent data to activity log
                        fetch(`${API_BASE}/api/v1/agent-activity`, {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                agent_type: task.assigned_agent, deal_id: dealId,
                                summary: `Completed: ${task.title}`, 
                                provider: result.provider || 'auto', 
                                confidence: result.confidence ?? 0.5,
                                reasoning: (result.reasoning || '').slice(0, 3000),
                                data: result.data || {},
                            }),
                        }).catch(() => { });

                    } else {
                        throw new Error(`Agent request failed (HTTP ${taskRes.status})`);
                    }
                } catch (error) {
                    failedCount++;
                    const detail = error instanceof Error ? error.message : 'Could not reach agent';
                    taskResult = { error: detail };
                    updateMessage(taskMsgId, { content: `⚠️ **${task.title}** — ${detail}`, status: 'error' });
                }
                }

                attemptedCount++;
                if (taskListId && task.id) {
                    try {
                        const saveRes = await fetch(`${API_BASE}/api/v1/tasks/${taskListId}/items/${task.id}`, {
                            method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ status: taskStatus, result: taskResult }),
                        });
                        if (!saveRes.ok) throw new Error(`HTTP ${saveRes.status}`);
                    } catch (error) {
                        failedCount++;
                        taskStatus = 'blocked';
                        updateMessage(taskMsgId, {
                            content: `⚠️ **${task.title}** — Agent result could not be persisted (${error instanceof Error ? error.message : 'network error'}); human review required.`,
                            status: 'error',
                        });
                    }
                }
                if (task.id) taskOutcomes.set(task.id, taskStatus);
                setExecutingProgress({ done: attemptedCount, total: taskList.length });
                updateMessage(progressId, {
                    content: `📊 **Progress:** ${attemptedCount}/${taskList.length} attempted · ${completedCount} succeeded · ${failedCount} failed`,
                });
                }));
            }

            // Emit SSE: phase changed to synthesizing
            fetch(
                `${API_BASE}/api/v1/stream/emit/${dealId}`,
                withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ event_type: 'phase_changed', data: { phase: 'synthesizing' } }),
                })
            ).catch(() => { });

            const dealScore = extractDealScore(completedResults);
            const needsReview = failedCount > 0;
            let statusPersistenceFailed = false;
            if (taskListId) {
                try {
                    const statusRes = await fetch(`${API_BASE}/api/v1/tasks/${taskListId}/status`, {
                        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: needsReview ? 'needs_review' : 'completed' }),
                    });
                    if (!statusRes.ok) statusPersistenceFailed = true;
                } catch {
                    statusPersistenceFailed = true;
                }
            }
            let finalNeedsReview = needsReview || statusPersistenceFailed;
            let recommendation = finalNeedsReview
                ? `${completedCount}/${taskList.length} tasks succeeded; ${failedCount} failed. Human review required.`
                : `${completedCount}/${taskList.length} tasks completed${dealScore === null ? '; deal not scored' : ''}.`;
            try {
                const dealStatusRes = await fetch(`${API_BASE}/api/v1/deals/${dealId}`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        status: finalNeedsReview ? 'needs_review' : 'completed',
                        current_stage: finalNeedsReview ? 'review' : 'completed',
                        ...(dealScore !== null ? { final_score: dealScore } : {}),
                        final_recommendation: recommendation,
                    }),
                });
                if (!dealStatusRes.ok) throw new Error(`HTTP ${dealStatusRes.status}`);
            } catch (error) {
                finalNeedsReview = true;
                recommendation = `${completedCount}/${taskList.length} tasks attempted; deal status could not be persisted. Human review required.`;
                addMessage({
                    role: 'system',
                    content: `⚠️ Analysis results may be saved, but the deal status could not be confirmed (${error instanceof Error ? error.message : 'network error'}). Refresh the deal before relying on its status.`,
                    status: 'error',
                });
                fetch(`${API_BASE}/api/v1/deals/${dealId}`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: 'needs_review', current_stage: 'review', final_recommendation: recommendation }),
                }).catch(() => { });
            }
            if (statusPersistenceFailed) {
                addMessage({
                    role: 'system',
                    content: '⚠️ Agent results are shown below, but task-list status could not be saved. Human review is required.',
                    status: 'error',
                });
            }

            // Emit SSE: deal complete
            if (!finalNeedsReview) {
                fetch(
                    `${API_BASE}/api/v1/stream/emit/${dealId}`,
                    withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        event_type: 'deal_complete',
                        data: {
                            final_score: dealScore === null ? null : Math.round(dealScore * 100),
                            recommendation,
                            agent_results: completedResults.map((r, i) => ({
                                agent_type: r._agent_type || taskList[i]?.assigned_agent,
                                confidence: r.confidence,
                                reasoning: r.reasoning?.slice(0, 500)
                            }))
                        }
                    }),
                    })
                ).catch(() => { });
            } else {
                fetch(
                    `${API_BASE}/api/v1/stream/emit/${dealId}`,
                    withAdminAuth({
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ event_type: 'deal_needs_review', data: { recommendation } }),
                    })
                ).catch(() => { });
            }

            // ─── Synthesis (Perplexity-style) ───
            setPhase('synthesizing');
            const companyName = extractCompanyName(userText);
            const synthesisContent = buildSynthesisMessage(completedResults, taskList, companyName, failedCount);
            addMessage({
                role: 'agent', agentName: 'DealForge Summary',
                content: synthesisContent,
                metadata: { _isSynthesis: true },
            });

            // Store completed results for follow-up context persistence
            completedResultsRef.current = completedResults.map((r, i) => ({
                ...r, agent: completedResults[i]?._agent_type || taskList[i]?.assigned_agent,
            }));
            taskListRef.current = taskList;
            setDealCompleted(!finalNeedsReview);

            const suggestions = generateContextAwareFollowUps(completedResults, taskList, companyName);
            setFollowUps(suggestions);

        } catch (err) {
            addMessage({
                role: 'system',
                content: `❌ Error: ${err instanceof Error ? err.message : 'Connection failed'}. Make sure the backend is running.`,
                status: 'error',
            });
        }

        setPhase('idle');
    }

    async function handleExport(format: 'json' | 'docx' = 'json') {
        try {
            const dealId = activeDealId;
            if (format === 'docx') {
                if (!dealId) throw new Error('Save or complete an analysis before downloading its Word report.');
                const response = await fetch(
                    `${API_BASE}/api/v1/deals/${dealId}/exports/docx`,
                    withAdminAuth()
                );
                if (!response.ok) {
                    const body = await response.json().catch(() => ({}));
                    throw new Error(body.detail || `Word report generation failed (HTTP ${response.status}).`);
                }
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `DealForge-${dealId.slice(0, 8)}.docx`;
                a.click();
                window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                return;
            }
            if (!dealId) throw new Error('Save or complete an analysis before downloading its structured data.');
            const response = await fetch(
                `${API_BASE}/api/v1/deals/${dealId}/exports/json`,
                withAdminAuth()
            );
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || `Structured data export failed (HTTP ${response.status}).`);
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `dealforge-analysis-${dealId.substring(0, 8)}.json`;
            a.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            addMessage({ role: 'system', content: `Export failed: ${error instanceof Error ? error.message : 'Could not create analysis export.'}`, status: 'error' });
        }
    }

    const handleCopy = (text: string, id: string) => {
        navigator.clipboard.writeText(text);
        setCopiedId(id);
        setTimeout(() => setCopiedId(null), 2000);
    };

    const handleEditSubmit = (msg: Message) => {
        if (editValue.trim() && editValue !== msg.content) updateMessage(msg.id, { content: editValue.trim() });
        setEditingMsgId(null);
    };

    const handleRegenerate = (msg: Message) => handleSend(msg.content, true);

    // Handle rating agent output (thumbs up/down) — selection persists per action
    const handleRateOutput = async (actionId: number | undefined, rating: number) => {
        if (!actionId) return;
        try {
            const res = await fetch(`${API_BASE}/api/v1/rate-output`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action_id: actionId,
                    rating: rating,
                    feedback: rating >= 4 ? 'positive' : 'negative',
                }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            setRatedIds(prev => ({ ...prev, [actionId]: rating >= 4 ? 'up' : 'down' }));
        } catch (err) {
            console.error('Failed to rate output:', err);
        }
    };

    // ─── Markdown renderer ───

    function renderMarkdown(text: string) {
        return (
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: ({ children }) => <h1 className="text-xl font-bold mt-4 mb-2 text-white">{children}</h1>,
                    h2: ({ children }) => <h2 className="text-lg font-bold mt-3 mb-1.5 text-white/90">{children}</h2>,
                    h3: ({ children }) => <h3 className="text-base font-bold mt-2 mb-1 text-white/85">{children}</h3>,
                    p: ({ children }) => <p className="mb-2 leading-relaxed">{children}</p>,
                    strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                    em: ({ children }) => <em className="italic text-white/70">{children}</em>,
                    pre: ({ children }) => <>{children}</>,
                    code: ({ className, children, ...props }) => {
                        const isBlock = className?.includes('language-');
                        return isBlock ? (
                            <CodeBlock className={className}>{children}</CodeBlock>
                        ) : (
                            <code className="bg-white/10 px-1.5 py-0.5 rounded text-xs font-mono text-cyan-300" {...props}>{children}</code>
                        );
                    },
                    ul: ({ children }) => <ul className="list-disc pl-5 space-y-1 mb-2">{children}</ul>,
                    ol: ({ children }) => <ol className="list-decimal pl-5 space-y-1 mb-2">{children}</ol>,
                    li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                    table: ({ children }) => (
                        <div className="overflow-x-auto my-2">
                            <table className="min-w-full border border-white/10 text-xs">{children}</table>
                        </div>
                    ),
                    th: ({ children }) => <th className="border border-white/10 px-2 py-1 bg-white/5 font-semibold text-left text-white/80">{children}</th>,
                    td: ({ children }) => <td className="border border-white/10 px-2 py-1 text-white/70">{children}</td>,
                    blockquote: ({ children }) => <blockquote className="border-l-4 border-cyan-500/30 pl-3 italic text-white/50 my-2">{children}</blockquote>,
                    a: ({ href, children }) => <a href={href} className="text-cyan-400 underline hover:text-cyan-300" target="_blank" rel="noreferrer">{children}</a>,
                }}
            >
                {text}
            </ReactMarkdown>
        );
    }

    // ─── Message renderer ───

    function renderMessage(msg: Message) {
        const isUser = msg.role === 'user';
        const messageContent = typeof msg.content === 'string'
            ? msg.content
            : 'Saved message content is unavailable.';
        const agentName = typeof msg.agentName === 'string' ? msg.agentName : '';
        const timestamp = msg.timestamp instanceof Date ? msg.timestamp : new Date(msg.timestamp || FALLBACK_TIMESTAMP);
        const agentKey = agentName.toLowerCase().replace(/\s/g, '_') || 'system';
        const agentStyle = AGENT_STYLES[agentKey] || AGENT_STYLES.system;
        const AgentIcon = agentStyle.icon;
        const isEditing = editingMsgId === msg.id;
        const sourcePrompt = messages.find(message => message.role === 'user')?.content || '';
        const conversationCompany = sourcePrompt ? extractCompanyName(sourcePrompt) : 'Target Company';
        const isSummaryMessage = msg.metadata?._isSynthesis || msg.agentName === 'DealForge Summary';
        const agentResultMessages = messages.filter(message =>
            message.role === 'agent' && Boolean(message.metadata?._agentSummary)
        );
        const sourceOnlyAgentMessages = agentResultMessages.filter(message =>
            message.metadata?.confidence_basis === 'not_calibrated_source_report'
            || message.metadata?.synthesis_status === 'deterministic_source_report'
        );
        const onlySourceReportAgents = sourceOnlyAgentMessages.length > 0
            && sourceOnlyAgentMessages.length === agentResultMessages.length;
        let displayedContent = conversationCompany !== 'Target Company'
            ? messageContent.replace(/Target Company/g, conversationCompany)
            : messageContent;
        if (agentName === 'Data Assistant' && onlySourceReportAgents && /employee count|team size/i.test(messageContent)) {
            displayedContent = 'Employee count is not required for this SEC revenue retrieval.';
        }
        if (isSummaryMessage && onlySourceReportAgents) {
            displayedContent = displayedContent.replace(/Overall Confidence:\s*\d+(?:\.\d+)?%/g, 'Confidence not calibrated for source-only retrieval');
        } else if (isSummaryMessage) {
            displayedContent = displayedContent.replace(/Overall Confidence:\s*(\d+(?:\.\d+)?)%/g, 'Mean model-reported score (uncalibrated): $1%');
        }
        const storedSummary = typeof msg.metadata?._agentSummary === 'string' ? msg.metadata._agentSummary : '';
        const storedAgentLabel = storedSummary.match(/^\*\*(.+?)\*\*/)?.[1];
        const nestedResult = msg.metadata?.result && typeof msg.metadata.result === 'object'
            ? msg.metadata.result as Record<string, unknown>
            : {};
        const nestedResultData = nestedResult.data && typeof nestedResult.data === 'object'
            ? nestedResult.data as Record<string, unknown>
            : nestedResult;
        const isUncalibratedSourceReport = msg.metadata?.confidence_basis === 'not_calibrated_source_report'
            || msg.metadata?.synthesis_status === 'deterministic_source_report'
            || nestedResultData.confidence_basis === 'not_calibrated_source_report'
            || nestedResultData.synthesis_status === 'deterministic_source_report';
        const displayedAgentSummary = isUncalibratedSourceReport
            ? formatAgentSummaryLine(msg.agentName || storedAgentLabel || 'Agent', {
                data: nestedResultData.confidence_basis ? nestedResultData : msg.metadata,
                execution_time_ms: msg.metadata?.execution_time_ms,
            })
            : storedSummary;

        return (
            <div key={msg.id} className={`flex gap-3 group ${isUser ? 'flex-row-reverse' : ''} animate-in fade-in slide-in-from-bottom-2 duration-300`}>
                {/* Avatar (shadcn Avatar + initials fallback) */}
                <Avatar className={`flex-shrink-0 w-9 h-9 rounded-xl shadow-lg
                    ${isUser
                        ? 'bg-gradient-to-br from-indigo-500 to-purple-600 shadow-indigo-500/20'
                        : `bg-gradient-to-br ${agentStyle.gradient} ${agentStyle.glow}`
                    }`}>
                    <AvatarFallback className="bg-transparent text-white rounded-xl" delayMs={600}>
                        {isUser
                            ? <User className="h-4 w-4 text-white" />
                            : <span className="flex items-center gap-1" title={agentName}>
                                <AgentIcon className="h-4 w-4 text-white" />
                                <span className="text-[8px] font-bold leading-none">{getInitials(agentName || 'DF')}</span>
                            </span>}
                    </AvatarFallback>
                </Avatar>

                <div className={`max-w-[85%] flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
                    {/* Agent Header with Rating Buttons */}
                    {!isUser && msg.agentName && (
                        <div className="flex items-center justify-between w-full mb-1.5">
                            <div className="flex items-center gap-2">
                                <span className="text-xs font-semibold text-white/60">{msg.agentName}</span>
                            {msg.status === 'thinking' && (
                                <span className="flex items-center gap-1 text-xs text-violet-400">
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    <span>Thinking<AnimatedDots /></span>
                                </span>
                            )}
                            {msg.status === 'done' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-medium">Done</span>
                            )}
                            {msg.status === 'error' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/15 text-red-400 font-medium">Error</span>
                            )}
                            {typeof msg.provider === 'string' && msg.provider && (
                                <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-white/5 text-white/40 font-medium">
                                    {msg.provider === 'ollama' || msg.provider === 'lmstudio' ? <Cpu className="h-2.5 w-2.5" /> : <Cloud className="h-2.5 w-2.5" />}
                                    {msg.provider}
                                </span>
                            )}
                            </div>
                            {/* Rating buttons for agent outputs - only show for completed agents */}
                            {msg.status === 'done' && msg.metadata?.action_id && (() => {
                                const actionId = msg.metadata!.action_id!;
                                const vote = ratedIds[actionId];
                                return (
                                    <div className="flex items-center gap-0.5">
                                        <button
                                            onClick={() => handleRateOutput(actionId, 5)}
                                            className={`p-1 rounded hover:bg-green-500/20 transition-colors ${vote === 'up' ? 'text-green-400 bg-green-500/10' : 'text-white/30 hover:text-green-400'}`}
                                            title="Good output"
                                        >
                                            <ThumbsUp className="h-3 w-3" />
                                        </button>
                                        <button
                                            onClick={() => handleRateOutput(actionId, 1)}
                                            className={`p-1 rounded hover:bg-red-500/20 transition-colors ${vote === 'down' ? 'text-red-400 bg-red-500/10' : 'text-white/30 hover:text-red-400'}`}
                                            title="Poor output"
                                        >
                                            <ThumbsDown className="h-3 w-3" />
                                        </button>
                                    </div>
                                );
                            })()}
                        </div>
                    )}

                    {/* Message Body */}
                    {isEditing ? (
                        <div className="w-full flex flex-col gap-2 mt-1">
                            <textarea
                                className="w-full text-sm p-3 rounded-xl border border-white/10 resize-none focus:outline-none focus:ring-1 focus:ring-cyan-500/50 min-h-[100px] text-white bg-white/5"
                                value={editValue} onChange={(e) => setEditValue(e.target.value)} autoFocus
                            />
                            <div className="flex justify-end gap-2">
                                <Button size="sm" variant="ghost" onClick={() => setEditingMsgId(null)} className="text-white/50 hover:text-white">Cancel</Button>
                                <Button size="sm" onClick={() => handleEditSubmit(msg)} className="bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30">Save</Button>
                            </div>
                        </div>
                    ) : displayedAgentSummary ? (
                        /* ─── Collapsible Agent Panel ─── */
                        <div className={`rounded-2xl text-sm leading-relaxed transition-all duration-300
                            bg-white/[0.04] backdrop-blur-sm rounded-bl-md border border-white/[0.06] text-white/80 hover:border-white/10 overflow-hidden`}>
                            <button
                                onClick={() => setCollapsedAgents(prev => ({ ...prev, [msg.id]: !prev[msg.id] }))}
                                className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.03] transition-colors cursor-pointer"
                            >
                                <span>{renderMarkdown(displayedAgentSummary)}</span>
                                {collapsedAgents[msg.id]
                                    ? <ChevronDown className="h-4 w-4 text-white/40 flex-shrink-0" />
                                    : <ChevronUp className="h-4 w-4 text-white/40 flex-shrink-0" />
                                }
                            </button>
                            {!collapsedAgents[msg.id] && (
                                <div className="px-4 pb-3 border-t border-white/[0.06] pt-3 animate-in fade-in slide-in-from-top-1 duration-200">
                                    {renderMarkdown(typeof msg.metadata?._agentDetail === 'string' ? msg.metadata._agentDetail : '')}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap transition-all duration-300
                            ${isUser
                                ? 'bg-gradient-to-r from-indigo-600/80 to-purple-600/80 text-white rounded-br-md border border-indigo-500/20 shadow-lg shadow-indigo-500/10'
                                : 'bg-white/[0.04] backdrop-blur-sm rounded-bl-md border border-white/[0.06] text-white/80 hover:border-white/10'
                            } ${msg.status === 'thinking' ? 'animate-pulse' : ''}`}>
                            {renderMarkdown(displayedContent)}
                            
                            {/* Scrum Master QA Controls (Tier 1 Enhancement) */}
                            {msg.metadata?.pending_clarification === true && (
                                <div className="mt-4 flex flex-wrap gap-2">
                                    <Button
                                        size="sm"
                                        onClick={() => {
                                            const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement;
                                            if (chatInput?.value.trim()) handleSend();
                                            else handleSend('confirm');
                                        }}
                                        className="bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border border-emerald-500/30 gap-1.5"
                                    >
                                        <CheckCircle2 className="h-3.5 w-3.5" />
                                        Answer & Continue
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => handleSend('skip')}
                                        className="border-amber-500/30 text-amber-500 hover:bg-amber-500/10 gap-1.5"
                                    >
                                        <RotateCcw className="h-3.5 w-3.5" />
                                        Skip Remaining
                                    </Button>
                                    {msg.metadata?.qa_controls?.can_ask_more === true && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => handleSend('more')}
                                            className="text-cyan-400 hover:bg-cyan-500/10 gap-1.5"
                                        >
                                            <Sparkles className="h-3.5 w-3.5" />
                                            Ask More
                                        </Button>
                                    )}
                                </div>
                            )}

                            {/* Assumptions Confirmation (Tier 2 Enhancement) */}
                            {msg.metadata?.is_assumptions_summary === true && (
                                <div className="mt-4 flex flex-wrap gap-2">
                                    <Button
                                        size="sm"
                                        onClick={() => handleSend('confirm')}
                                        className="bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 border border-indigo-500/30 gap-1.5"
                                    >
                                        <Zap className="h-3.5 w-3.5" />
                                        Confirm & Proceed
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => {
                                            const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement;
                                            chatInput?.focus();
                                        }}
                                        className="border-white/10 text-white/60 hover:text-white"
                                    >
                                        Modify Assumptions
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}


                    {/* Sources / citations (RAG v2) + confidence */}
                    {!isUser && !isEditing && (() => {
                        const cites = normalizeCitations(msg.metadata?.citations);
                        const conf = isUncalibratedSourceReport
                            ? undefined
                            : msg.metadata?.confidence;
                        if (cites.length === 0 && typeof conf !== 'number') return null;
                        return (
                            <div className="mt-2 w-full space-y-1.5">
                                {typeof conf === 'number' && (
                                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 font-medium">
                                        <CheckCircle2 className="h-2.5 w-2.5" />
                                        Confidence {Math.round(conf * 100)}%
                                    </span>
                                )}
                                {cites.length > 0 && (
                                    <details className="rounded-xl border border-white/[0.06] bg-white/[0.02] overflow-hidden">
                                        <summary className="flex items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-white/50 hover:text-white/80 cursor-pointer list-none">
                                            <Quote className="h-3 w-3 text-cyan-400" />
                                            Sources ({cites.length})
                                        </summary>
                                        <ul className="px-3 pb-2.5 space-y-1.5">
                                            {cites.map((c, i) => (
                                                <li key={i} className="flex items-start gap-1.5 text-[11px] leading-relaxed text-white/60">
                                                    <span className="flex-shrink-0 w-4 h-4 rounded bg-cyan-500/15 text-cyan-400 text-[9px] font-bold flex items-center justify-center mt-0.5">{i + 1}</span>
                                                    <span>
                                                        <span className="font-semibold text-white/75">{c.label}</span>
                                                        {c.source && <span className="text-white/40"> — {c.source}</span>}
                                                    </span>
                                                </li>
                                            ))}
                                        </ul>
                                    </details>
                                )}
                            </div>
                        );
                    })()}

                    {/* Action Buttons */}
                    {!isEditing && (
                        <div className={`flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${isUser ? 'flex-row-reverse' : ''}`}>
                            <span className="text-[10px] text-white/30 px-1">
                                {timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </span>
                            <button className="p-1 rounded-md text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors" onClick={() => handleCopy(messageContent, msg.id)} title="Copy">
                                {copiedId === msg.id ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                            </button>
                            {Boolean(msg.metadata?.excel_model_base64) && (
                                <a
                                    href={`data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${msg.metadata?.excel_model_base64 as string}`}
                                    download={`Financial_Model_${(msg.metadata?.deal_id as string) || 'export'}.xlsx`}
                                    className="p-1 text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/10 rounded-md transition-colors"
                                    title="Download Excel Model"
                                >
                                    <Download className="h-3 w-3" />
                                </a>
                            )}
                            {isUser && !isProcessing && (
                                <>
                                    <button className="p-1 rounded-md text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors" onClick={() => { setEditingMsgId(msg.id); setEditValue(messageContent); }} title="Edit">
                                        <Edit2 className="h-3 w-3" />
                                    </button>
                                    <button className="p-1 rounded-md text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors" onClick={() => handleRegenerate(msg)} title="Regenerate">
                                        <RotateCcw className="h-3 w-3" />
                                    </button>
                                </>
                            )}
                            {!isUser && msg.status === 'error' && !isProcessing && (() => {
                                const lastUser = [...messages].reverse().find(m => m.role === 'user');
                                if (!lastUser) return null;
                                return (
                                    <button
                                        className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 transition-colors"
                                        onClick={() => handleSend(lastUser.content, true)}
                                        title="Retry last request"
                                    >
                                        <RotateCcw className="h-3 w-3" /> Retry
                                    </button>
                                );
                            })()}
                        </div>
                    )}
                </div>
            </div>
        );
    }

    // ─── Phase indicator bar ───

    const phaseLabel = PHASE_LABELS[phase] || PHASE_LABELS.idle;

    // ═══════════════════════════════════════
    //  RENDER
    // ═══════════════════════════════════════

    return (
        <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-slate-950">
            {/* Header */}
            <div className="flex min-h-16 shrink-0 items-center justify-between gap-3 border-b border-white/10 px-4 sm:px-6">
                <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-white sm:text-lg">
                        {activeConv?.title || 'Deal analysis'}
                    </h2>
                    <div className="mt-1 flex items-center gap-2 text-xs text-white/50">
                        <span className={`h-2 w-2 rounded-full ${isProcessing ? 'animate-pulse bg-amber-400' : 'bg-emerald-400'}`} />
                        <span>{isProcessing ? `${phaseLabel.text}${phase === 'executing' ? ` · ${executingProgress.done}/${executingProgress.total} agents` : ''}` : dealCompleted ? 'Analysis complete' : 'Ready for analysis'}</span>
                        {activeDealId && <span className="hidden border-l border-white/20 pl-2 sm:inline">Deal {activeDealId.substring(0, 8)}</span>}
                        {currentTurnAgentError && <span className="border-l border-white/20 pl-2 text-rose-300">Agent error in this turn</span>}
                    </div>
                </div>
                {messages.length > 1 && (
                    <div className="flex shrink-0 items-center gap-1.5">
                        <Button variant="outline" size="sm" onClick={() => handleExport('docx')} title="Download Word report (.docx)" className="h-8 border-white/15 bg-transparent px-2.5 text-white/75 hover:bg-white/10 hover:text-white sm:px-3">
                            <FileText className="h-3.5 w-3.5 sm:mr-2" /><span className="hidden sm:inline">Report</span>
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => handleExport('json')} title="Export structured analysis data" className="h-8 border-white/15 bg-transparent px-2.5 text-white/75 hover:bg-white/10 hover:text-white sm:px-3">
                            <Download className="h-3.5 w-3.5 sm:mr-2" /><span className="hidden sm:inline">Data</span>
                        </Button>
                    </div>
                )}
            </div>

            {/* Chat Container */}
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden rounded-2xl border border-white/[0.06] bg-[#0d0d1a]/80 backdrop-blur-xl shadow-2xl shadow-black/30">

                {/* Messages */}
                <ScrollArea className="min-h-0 flex-1" type="always">
                    <div className="mx-auto w-full max-w-5xl space-y-5 px-3 py-5 sm:px-6 sm:py-6">
                        <div role="log" aria-live="polite" aria-label="Conversation" className="space-y-5">
                            {/* renderMessage only wires event handlers; refs are read in those handlers, not during render. */}
                            {/* eslint-disable-next-line react-hooks/refs */}
                            {messages.map(renderMessage)}
                        </div>
                        {!isProcessing && !messages.some(m => m.role === 'user') && (
                            <StarterPrompts hasDeal={Boolean(activeDealId)} onPick={q => handleSend(q)} />
                        )}
                        <div ref={messagesEndRef} className="h-1 w-full" />
                    </div>
                </ScrollArea>
                {!atBottom && messages.length > 0 && (
                    <div className="pointer-events-none relative z-10 h-0">
                        <button
                            type="button"
                            onClick={scrollToBottom}
                            aria-label="Jump to latest message"
                            className="pointer-events-auto absolute -top-12 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-white/15 bg-slate-900/95 px-3 py-1.5 text-xs text-white/80 shadow-lg backdrop-blur transition-colors hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
                        >
                            <ChevronDown className="h-3.5 w-3.5" /> Jump to latest
                        </button>
                    </div>
                )}

                {approvalRequest && (
                    <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-amber-300/20 bg-amber-300/[0.06] px-4 py-3 sm:px-6" role="status">
                        <div className="flex min-w-0 items-center gap-2 text-sm text-amber-100">
                            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-300" />
                            <span>{approvalRequest.label ?? `Review the ${approvalRequest.taskCount}-task plan above. Nothing runs until you approve.`}</span>
                        </div>
                        <div className="ml-auto flex shrink-0 items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => approvalRequest.resolve(false)} className="border-white/15 bg-transparent text-white/75 hover:bg-white/10">
                                Cancel
                            </Button>
                            <Button size="sm" disabled={!approvalArmed} onClick={() => approvalRequest.resolve(true)}>
                                <CheckCircle2 className="mr-2 h-4 w-4" /> Approve &amp; run
                            </Button>
                        </div>
                    </div>
                )}

                {/* Follow-up Suggestions */}
                {followUps.length > 0 && !isProcessing && (
                    <div className="shrink-0 border-t border-white/10 px-4 py-3 sm:px-6">
                        <div className="flex items-center gap-2 mb-2">
                            <HelpCircle className="h-3.5 w-3.5 text-cyan-400" />
                            <span className="text-xs font-semibold text-cyan-400">Follow-up questions</span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {followUps.map((q, i) => (
                                <button key={i} onClick={() => handleSend(q)}
                                    className="text-xs px-3 py-1.5 rounded-full border border-white/10 bg-white/[0.03] hover:bg-white/[0.08] hover:border-cyan-500/30 transition-all duration-200 flex items-center gap-1 text-white/60 hover:text-white group"
                                >
                                    <ChevronRight className="h-3 w-3 text-white/30 group-hover:text-cyan-400 transition-colors" />
                                    {q}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* File Chips */}
                {uploadedFiles.length > 0 && (
                    <div className="px-5 pb-2 flex flex-wrap gap-2">
                        {uploadedFiles.map((f, i) => (
                            <span key={i} className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 text-white/60">
                                <Paperclip className="h-3 w-3" />
                                {f.name}
                                <button onClick={() => setUploadedFiles(prev => prev.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`} className="ml-1 text-white/30 hover:text-white/60">×</button>
                            </span>
                        ))}
                    </div>
                )}

                {/* ═══ Input Area ═══ */}
                <div className="shrink-0 border-t border-white/10 bg-slate-950 px-3 py-3 sm:px-6 sm:py-4">
                    {/* Textarea */}
                    <div className="relative rounded-xl border border-white/10 bg-white/[0.03] focus-within:border-cyan-500/30 focus-within:bg-white/[0.05] transition-all duration-300">
                        <textarea
                            ref={textareaRef}
                            id="chat-input"
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleSend(); } }}
                            aria-label="Message DealForge"
                            placeholder={followUps.length > 0 && !isProcessing ? 'Ask a follow-up...' : 'Ask anything...'}
                            className="w-full min-h-[48px] max-h-[150px] resize-none bg-transparent px-4 py-3.5 pr-12 text-sm text-white placeholder:text-white/30 focus:outline-none"
                            rows={1}
                            disabled={isProcessing}
                        />

                        {/* Send button inside textarea */}
                        <button
                            onClick={() => handleSend()}
                            aria-label={isProcessing ? 'Working' : 'Send message'}
                            disabled={isProcessing || (!input.trim() && uploadedFiles.length === 0)}
                            className="absolute right-3 bottom-3 w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-200
                                disabled:opacity-30 disabled:cursor-not-allowed
                                bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white shadow-lg shadow-cyan-500/20"
                        >
                            {isProcessing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        </button>
                    </div>

                    {/* Toolbar Row */}
                    <div className="flex items-center justify-between mt-2.5">
                        <div className="flex items-center gap-2">
                            {/* Focus Mode */}
                            <FocusModePopover
                                value={focusMode}
                                onChange={setFocusMode}
                                open={focusOpen}
                                onToggle={() => setFocusOpen(!focusOpen)}
                            />

                            {/* Source Toggles */}
                            <SourceToggles active={activeSources} onToggle={toggleSource} />

                            {/* Attach */}
                            <input ref={fileInputRef} type="file" className="hidden" multiple accept=".pdf,.docx,.xlsx,.csv,.txt,.md"
                                onChange={e => { if (e.target.files) setUploadedFiles(prev => [...prev, ...Array.from(e.target.files!)]); }}
                            />
                            <button
                                onClick={() => fileInputRef.current?.click()}
                                aria-label="Attach files"
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 text-white/50 hover:text-white/70"
                            >
                                <Paperclip className="h-3.5 w-3.5" />
                            </button>
                        </div>

                        {/* Phase Indicator */}
                        <div className="flex items-center gap-2">
                            {phase !== 'idle' && (
                                <span className={`flex items-center gap-1.5 text-xs font-medium ${phaseLabel.color}`}>
                                    <span className="relative flex h-2 w-2">
                                        <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${phaseLabel.color.replace('text-', 'bg-')}`}></span>
                                        <span className={`relative inline-flex rounded-full h-2 w-2 ${phaseLabel.color.replace('text-', 'bg-')}`}></span>
                                    </span>
                                    {phaseLabel.text}
                                    {phase === 'executing' && ` (${executingProgress.done}/${executingProgress.total})`}
                                    <AnimatedDots />
                                </span>
                            )}
                            {phase === 'idle' && activeDealId && (
                                <span className="text-[10px] text-white/20">Deal {activeDealId.substring(0, 8)}</span>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
