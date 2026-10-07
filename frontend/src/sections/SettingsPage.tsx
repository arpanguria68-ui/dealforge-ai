import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import {
    Key, Brain, Shield, Eye, EyeOff, Save,
    CheckCircle, XCircle, Loader2, Cpu, Cloud, RefreshCw,
    Database, Zap, AlertTriangle, Palette, FileText
} from 'lucide-react';
import { ApiUsageMonitor } from './ApiUsageMonitor';
import { API_BASE, withAdminAuth } from '@/lib/api-base';

const PROVIDER_OPTIONS = [
    { value: 'gemini', label: 'Google Gemini', icon: Cloud, color: 'text-blue-500' },
    { value: 'openai', label: 'OpenAI', icon: Cloud, color: 'text-green-500' },
    { value: 'openrouter', label: 'OpenRouter', icon: Cloud, color: 'text-cyan-600' },
    { value: 'mistral', label: 'Mistral AI', icon: Cloud, color: 'text-orange-500' },
    { value: 'vertex', label: 'Google Vertex AI', icon: Cloud, color: 'text-purple-600' },
    { value: 'nvidia', label: 'NVIDIA NIM', icon: Cloud, color: 'text-lime-500' },
    { value: 'claude', label: 'Anthropic Claude', icon: Cloud, color: 'text-amber-500' },
    { value: 'groq', label: 'Groq', icon: Cloud, color: 'text-rose-500' },
    { value: 'ollama', label: 'Ollama (Local)', icon: Cpu, color: 'text-purple-500' },
    { value: 'lmstudio', label: 'LM Studio (Local)', icon: Cpu, color: 'text-pink-500' },
];

const AGENTS = [
    { name: 'project_manager', label: '🧩 Scrum Master / PM', desc: 'Reasoning planner — identifies data needs, asks questions, creates risk-annotated task plans', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Complex Reasoning)' },
    { name: 'financial_analyst', label: 'Financial Analyst', desc: 'DCF, valuation, investment thesis', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Large Context Finance)' },
    { name: 'valuation_agent', label: 'Valuation Agent', desc: 'Multi-method valuation math', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Valuation Precision)' },
    { name: 'dcf_lbo_architect', label: 'DCF / LBO Architect', desc: 'LBO modeling, debt waterfalls, DCF construction', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (LBO/DCF Precision)' },
    { name: 'legal_advisor', label: 'Legal Advisor', desc: 'Contract analysis, legal risk', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Contract Analysis)' },
    { name: 'risk_assessor', label: 'Risk Assessor', desc: '7-category risk framework', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Strategic Risk)' },
    { name: 'debate_moderator', label: 'Debate Moderator', desc: 'Synthesizing viewpoints', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Multi-Perspective)' },
    { name: 'market_researcher', label: 'Market Researcher', desc: 'Market sizing, competitor lists', complexity: 'low', recommendedModel: 'Gemini 1.5 Flash (Fast Web Search)' },
    { name: 'market_risk_agent', label: 'Market Risk Agent', desc: 'Rule-based risk scoring', complexity: 'low', recommendedModel: 'Gemini 1.5 Flash (Risk Scoring)' },
    { name: 'compliance_agent', label: 'Compliance Agent', desc: 'Checklist processing', complexity: 'low', recommendedModel: 'Gemini 1.5 Pro (Regulatory Compliance)' },
    { name: 'scoring_agent', label: 'Scoring Agent', desc: 'Aggregation + formatting', complexity: 'low', recommendedModel: 'Gemini 1.5 Flash (Data Aggregation)' },
    { name: 'pageindex', label: '📄 PageIndex (RAG)', desc: 'Knowledge Base search and Retrieval QA', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Embedding + Retrieval)' },
    { name: 'advanced_financial_modeler', label: 'Advanced Financial Modeler', desc: 'LBO/DCF Excel modeling w/ Monte Carlo', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Precision Modeling)' },
    { name: 'complex_reasoning', label: 'Complex Reasoning Agent', desc: 'Explicit Chain-of-Thought logic & gap detection', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Chain-of-Thought)' },
    { name: 'data_curator', label: 'Data Curator', desc: 'Data synthesis, conflict resolution, & normalization', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Data Synthesis)' },
    { name: 'report_architect', label: 'Report Architect', desc: 'Dynamic report blueprints & branding config', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (High-Fidelity Synthesis)' },
    { name: 'due_diligence_agent', label: 'Due Diligence Agent', desc: 'Commercial due diligence, peer identification', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Strategic Analysis)' },
    { name: 'investment_memo_agent', label: 'Investment Memo Agent', desc: 'Investment memo drafting, executive summary', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Executive Writing)' },
    { name: 'red_team', label: 'Red Team', desc: 'Stress-tests assumptions with adversarial analysis', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Adversarial Logic)' },
    { name: 'business_analyst', label: 'Business Analyst', desc: 'Business model analysis, unit economics', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Unit Economics)' },
    { name: 'esg_agent', label: 'ESG Agent', desc: 'ESG risk scoring, carbon footprint, supply chain risk', complexity: 'low', recommendedModel: 'Gemini 1.5 Pro (ESG Compliance)' },
    { name: 'integration_planner_agent', label: 'Integration Planner', desc: 'Post-merger integration planning, synergy tracking', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Integration Planning)' },
    { name: 'compiler_agent', label: 'Report Compiler', desc: 'Final IC package generation (PPTX, Excel, PDF)', complexity: 'high', recommendedModel: 'Mistral Large 2 or Gemini 1.5 Pro (High-Fidelity Synthesis)' },
    { name: 'treasury_agent', label: '🏦 Treasury Cash Agent', desc: 'Cash positioning, liquidity forecasting, currency exposure', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Treasury Analysis)' },
    { name: 'fpa_forecasting_agent', label: '📈 FP&A Forecasting', desc: 'Scenario modeling, variance analysis, financial planning', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Scenario Modeling)' },
    { name: 'tax_compliance_agent', label: '📊 Tax Compliance', desc: 'Tax provision calculations, transfer pricing, regulatory compliance', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Tax & Regulatory)' },
    { name: 'ofas_supervisor', label: '🎯 OFAS Supervisor', desc: 'Orchestrates multi-agent analysis with RACI delegation', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Orchestration)' },
    { name: 'prospectus_agent', label: '📜 Prospectus Processor', desc: 'S-1/10-K/10-Q filing extraction, structured KPI data', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Document Extraction)' },
    { name: 'compliance_qa_agent', label: '✅ Compliance QA', desc: 'Validates deliverables before IC submission', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Regulatory Compliance)' },
    { name: 'ai_tech_diligence_agent', label: '🤖 AI/Tech Diligence', desc: 'AI/ML stack assessment, tech value quantification', complexity: 'high', recommendedModel: 'Gemini 1.5 Pro (Technical Architecture)' },
];

interface MCPProviderConfig {
    key: string;
    status: 'idle' | 'testing' | 'connected' | 'error';
    errorMsg?: string;
    latency?: number;
}

interface ProviderModel {
    id: string;
    name: string;
    context_window?: number;
    daily_limit?: number | string;
    [key: string]: unknown;
}

interface ProviderModelsResponse {
    status: string;
    models: ProviderModel[];
}

interface LayaStatus {
    backend: string;
    mode: string;
    lmstudio?: { model?: string; model_source?: string; loaded_models?: string[]; reachable?: boolean; models?: string[]; base_url?: string };
    remote?: { url?: string };
    local?: { installed?: boolean };
}

const MCP_PROVIDERS_CONFIG = [
    {
        id: 'finnhub',
        name: 'Finnhub',
        description: 'Real-time stock prices, financials, earnings, news, analyst ratings',
        icon: '📈',
        defaultKey: '',
        docsUrl: 'https://finnhub.io',
        capabilities: ['Stock Price', 'Financials', 'Earnings', 'News', 'Sentiment'],
    },
    {
        id: 'massive',
        name: 'Massive.com',
        description: 'U.S. market data, ticker reference, corporate actions, and market status',
        icon: '🏢',
        defaultKey: '',
        docsUrl: 'https://massive.com',
        capabilities: ['Market Status', 'Stock Prices', 'Ticker Reference', 'Corporate Actions'],
    },
    {
        id: 'fmp',
        name: 'Financial Modeling Prep',
        description: 'Comprehensive financial statements, 150+ ratios, DCF, WACC, and market data',
        icon: '📊',
        defaultKey: '',
        docsUrl: 'https://financialmodelingprep.com',
        capabilities: ['Financials', 'Valuation Models', 'Ratios', 'Real-time Prices'],
    },
    {
        id: 'alpha_vantage',
        name: 'Alpha Vantage',
        description: 'Prices, fundamentals, and 50+ technical indicators',
        icon: '📈',
        defaultKey: '',
        docsUrl: 'https://www.alphavantage.co/',
        capabilities: ['Stock Price', 'Technical Indicators', 'Forex', 'Commodities'],
    },
    {
        id: 'financial_datasets',
        name: 'Financial Datasets',
        description: 'Statements, real-time prices, news, crypto',
        icon: '📰',
        defaultKey: '',
        docsUrl: 'https://financialdatasets.ai/',
        capabilities: ['Financials', 'Stock Price', 'News', 'Crypto'],
    },
    {
        id: 'serper',
        name: 'Serper.dev',
        description: 'Fast Google Search API for agentic web retrieval',
        icon: '🔍',
        defaultKey: '',
        docsUrl: 'https://serper.dev',
        capabilities: ['Web Search', 'News Search', 'Places'],
    },
    {
        id: 'searxng',
        name: 'SearXNG',
        description: 'Privacy-respecting meta-search engine (Self-hosted)',
        icon: '♻️',
        defaultKey: 'http://localhost:8080',
        docsUrl: 'https://docs.searxng.org',
        capabilities: ['Web Search', 'Private Search'],
    },
    {
        id: 'ddg',
        name: 'DuckDuckGo',
        description: 'Privacy-focused search (No API key required)',
        icon: '🦆',
        defaultKey: 'NO_KEY_REQUIRED',
        docsUrl: 'https://duckduckgo.com',
        capabilities: ['Web Search'],
    },
    {
        id: 'sec_api',
        name: 'SEC API (sec-api.io)',
        description: 'Advanced 10-K/8-K filing due diligence with NLP change detection, abnormality flags, ROA/ROE impact prediction. Free EDGAR API used by default.',
        icon: '📋',
        defaultKey: '',
        docsUrl: 'https://sec-api.io',
        capabilities: ['Filing DD', '10-K/8-K Analysis', 'Change Detection', 'Event Clustering'],
    },
];

interface SettingsState {
    // API Keys
    gemini_api_key: string;
    openai_api_key: string;
    openrouter_api_key: string;
    mistral_api_key: string;
    vertex_api_key: string;
    nvidia_api_key: string;
    anthropic_api_key: string;
    groq_api_key: string;
    // Laya System-1 decision layer
    laya: {
        enabled: boolean;
        mode: string;
        base_url: string;
        lmstudio_url: string;
        lmstudio_model: string;
        fast_pool: string;
        general_pool: string;
        reasoning_pool: string;
        fast_model: string;
        general_model: string;
        reasoning_model: string;
    };
    // RAG v2 tuning
    rag: {
        dense: boolean;
        laya_rerank: boolean;
        rerank_n: number;
        w_tree: number;
        w_bm25: number;
        w_dense: number;
    };
    // Cloud Model Names
    gemini_model: string;
    openai_model: string;
    openrouter_model: string;
    mistral_model: string;
    vertex_model: string;
    vertex_project_id: string;
    vertex_location: string;
    nvidia_model: string;
    nvidia_base_url: string;
    // Local LLMs
    ollama_base_url: string;
    ollama_model: string;
    lmstudio_base_url: string;
    lmstudio_model: string;
    // Default provider
    default_llm_provider: string;
    // Agent routing
    agent_routing: Record<string, string>;
    // PageIndex
    pageindex_mode: string;
    // Gateway Cost Controls
    gateway: {
        gemini_max_rpm: number;
        gemini_max_tpm: number;
        openai_max_rpm: number;
        openai_max_tpm: number;
        mistral_max_rpm: number;
        mistral_max_tpm: number;
        cache_enabled: boolean;
        hybrid_compression: boolean;
        daily_budget_usd: number;
    };
    // Web Search
    search_priority: string[];
    serper_api_key: string;
    searxng_instance_url: string;
    searxng_api_key: string;
    fmp_api_key: string;
    financial_datasets_api_key: string;
    alpha_vantage_api_key: string;
    finnhub_api_key: string;
    sec_api_key: string;
    // Document Design & Branding
    branding: {
        primary_color: string;
        secondary_color: string;
        font_family: string;
    };
    report_preferences: {
        target_audience: string;
        industry_focus: string;
    };
}

export function SettingsPage() {
    const [settings, setSettings] = useState<SettingsState>({
        gemini_api_key: '',
        openai_api_key: '',
        openrouter_api_key: '',
        mistral_api_key: '',
        vertex_api_key: '',
        nvidia_api_key: '',
        anthropic_api_key: '',
        groq_api_key: '',
        laya: {
            enabled: true,
            mode: 'auto',
            base_url: '',
            lmstudio_url: '',
            lmstudio_model: '',
            fast_pool: 'groq,gemini,mistral',
            general_pool: 'gemini,openai,openrouter,mistral,nvidia,vertex',
            reasoning_pool: 'vertex,claude,openai,openrouter,gemini',
            fast_model: '',
            general_model: '',
            reasoning_model: '',
        },
        rag: {
            dense: true,
            laya_rerank: true,
            rerank_n: 30,
            w_tree: 0.4,
            w_bm25: 0.3,
            w_dense: 0.3,
        },
        gemini_model: '',
        openai_model: '',
        openrouter_model: 'openai/gpt-4o-mini',
        mistral_model: '',
        vertex_model: 'gemini-3.8-flash',
        vertex_project_id: '',
        vertex_location: 'us-central1',
        nvidia_model: 'z-ai/glm-5.3',
        nvidia_base_url: 'https://integrate.api.nvidia.com/v1',
        ollama_base_url: 'http://localhost:11434',
        ollama_model: 'llama3',
        lmstudio_base_url: 'http://localhost:1234/v1',
        lmstudio_model: 'local-model',
        default_llm_provider: 'gemini',
        agent_routing: {
            financial_analyst: 'gemini',
            valuation_agent: 'gemini',
            dcf_lbo_architect: 'gemini',
            legal_advisor: 'gemini',
            risk_assessor: 'gemini',
            debate_moderator: 'gemini',
            market_researcher: 'ollama',
            market_risk_agent: 'ollama',
            compliance_agent: 'ollama',
            scoring_agent: 'ollama',
            pageindex: 'gemini',
            advanced_financial_modeler: 'gemini',
            complex_reasoning: 'gemini',
            data_curator: 'gemini',
            report_architect: 'gemini',
            due_diligence_agent: 'gemini',
            investment_memo_agent: 'gemini',
            red_team: 'gemini',
            business_analyst: 'gemini',
            esg_agent: 'ollama',
            integration_planner_agent: 'gemini',
            project_manager: 'gemini',
            compiler_agent: 'gemini',
            treasury_agent: 'gemini',
            fpa_forecasting_agent: 'gemini',
            tax_compliance_agent: 'gemini',
            ofas_supervisor: 'gemini',
            prospectus_agent: 'gemini',
            compliance_qa_agent: 'gemini',
            ai_tech_diligence_agent: 'gemini',
        },
        pageindex_mode: 'local',
        gateway: {
            gemini_max_rpm: 12,
            gemini_max_tpm: 80000,
            openai_max_rpm: 50,
            openai_max_tpm: 150000,
            mistral_max_rpm: 5,
            mistral_max_tpm: 400000,
            cache_enabled: true,
            hybrid_compression: true,
            daily_budget_usd: 0,
        },
        search_priority: ['serper', 'searxng', 'ddg'],
        serper_api_key: '',
        searxng_instance_url: 'http://localhost:8080',
        searxng_api_key: '',
        fmp_api_key: '',
        financial_datasets_api_key: '',
        alpha_vantage_api_key: '',
        finnhub_api_key: '',
        sec_api_key: '',
        branding: {
            primary_color: '#003366',
            secondary_color: '#E0E7FF',
            font_family: 'Helvetica',
        },
        report_preferences: {
            target_audience: 'Investment Committee',
            industry_focus: 'General',
        },
    });

    const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [saveError, setSaveError] = useState('');
    const [apiKeyConfigured, setApiKeyConfigured] = useState<Record<string, boolean>>({});
    const [ollamaStatus, setOllamaStatus] = useState<'checking' | 'online' | 'offline'>('checking');
    const [lmstudioStatus, setLmstudioStatus] = useState<'checking' | 'online' | 'offline'>('checking');
    const [availableModels, setAvailableModels] = useState<Record<string, ProviderModelsResponse>>({});
    const [fetchingModels, setFetchingModels] = useState(false);
    const [mcpState, setMcpState] = useState<Record<string, MCPProviderConfig>>(() =>
        Object.fromEntries(
            MCP_PROVIDERS_CONFIG.map(p => [p.id, { key: p.defaultKey, status: 'idle' as const }])
        )
    );

    const [cloudApiTestStatus, setCloudApiTestStatus] = useState<Record<string, { status: 'idle' | 'testing' | 'connected' | 'error', errorMsg?: string, detail?: string }>>({
        gemini: { status: 'idle' },
        openai: { status: 'idle' },
        openrouter: { status: 'idle' },
        mistral: { status: 'idle' },
        vertex: { status: 'idle' },
        nvidia: { status: 'idle' }
    });

    // Fetch live model lists from all providers
    const fetchModels = useCallback(async () => {
        setFetchingModels(true);
        try {
            const res = await fetch(
                `${API_BASE}/api/v1/models/available`,
                withAdminAuth({ signal: AbortSignal.timeout(15000) })
            );
            if (res.ok) {
                const data = await res.json() as Record<string, ProviderModelsResponse>;
                setAvailableModels(data);
                // Update local LLM status from the response
                if (data.ollama) setOllamaStatus(data.ollama.status === 'online' ? 'online' : 'offline');
                if (data.lmstudio) setLmstudioStatus(data.lmstudio.status === 'online' ? 'online' : 'offline');

                // Auto-select first model if current selection is empty or not in live list
                setSettings(prev => {
                    const updates: Record<string, string> = {};
                    const map: [string, keyof SettingsState][] = [
                        ['gemini', 'gemini_model'],
                        ['openai', 'openai_model'],
                        ['openrouter', 'openrouter_model'],
                        ['mistral', 'mistral_model'],
                        ['nvidia', 'nvidia_model'],
                        ['ollama', 'ollama_model'],
                        ['lmstudio', 'lmstudio_model'],
                        ['vertex', 'vertex_model'],
                    ];
                    for (const [provider, field] of map) {
                        const models = data[provider]?.models || [];
                        const cur = prev[field] as string || '';
                        if (models.length > 0 && (!cur || !models.some(m => m.id === cur))) {
                            updates[field as string] = models[0].id;
                        }
                    }
                    return Object.keys(updates).length ? { ...prev, ...updates } : prev;
                });
            }
        } catch (e) {
            console.error('Failed to fetch models', e);
        }
        setFetchingModels(false);
    }, []);

    async function testCloudApi(providerId: string) {
        let apiKey = '';
        if (providerId === 'gemini') apiKey = settings.gemini_api_key;
        if (providerId === 'openai') apiKey = settings.openai_api_key;
        if (providerId === 'openrouter') apiKey = settings.openrouter_api_key;
        if (providerId === 'mistral') apiKey = settings.mistral_api_key;
        if (providerId === 'vertex') apiKey = settings.vertex_api_key;
        if (providerId === 'nvidia') apiKey = settings.nvidia_api_key;

        if (!apiKey) {
            setCloudApiTestStatus(prev => ({ ...prev, [providerId]: { status: 'error', errorMsg: 'API Key is missing' } }));
            return;
        }

        setCloudApiTestStatus(prev => ({ ...prev, [providerId]: { status: 'testing' } }));
        try {
            const body: Record<string, string> = { provider: providerId, api_key: apiKey };
            const selectedModel = settings[`${providerId}_model` as keyof typeof settings];
            if (typeof selectedModel === 'string') body.model = selectedModel;
            // For Vertex AI, also send project_id and location so the backend can make a real test call
            if (providerId === 'vertex') {
                body.project_id = settings.vertex_project_id || '';
                body.location = settings.vertex_location || 'us-central1';
            }
            if (providerId === 'nvidia') body.base_url = settings.nvidia_base_url;
            const res = await fetch(
                `${API_BASE}/api/v1/models/test`,
                withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                })
            );
            const data = await res.json() as { ok?: boolean; models?: ProviderModel[]; error?: string; generation?: { model?: string; latency_ms?: number } };
            const models = data.models;
            if (data.ok && models) {
                const persist = await fetch(
                    `${API_BASE}/api/v1/settings`,
                    withAdminAuth({
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ [`${providerId}_api_key`]: apiKey }),
                    })
                );
                if (!persist.ok) throw new Error(`Key tested, but saving failed (HTTP ${persist.status}).`);
                setApiKeyConfigured(prev => ({ ...prev, [providerId]: true }));
                setCloudApiTestStatus(prev => ({
                    ...prev,
                    [providerId]: { status: 'connected', detail: `Generation verified: ${data.generation?.model || selectedModel || providerId}${typeof data.generation?.latency_ms === 'number' ? ` · ${data.generation.latency_ms} ms` : ''}` },
                }));
                // Update live models for this provider so Cloud Model Selection immediately populates
                setAvailableModels(prev => ({
                    ...prev,
                    [providerId]: { status: 'online', models }
                }));
                // Auto-select first model if none is currently selected
                if (models.length > 0) {
                    setSettings(prev => {
                        const cur = prev[`${providerId}_model` as keyof typeof prev];
                        if (!cur || !models.some(m => m.id === cur)) {
                            return { ...prev, [`${providerId}_model`]: models[0].id };
                        }
                        return prev;
                    });
                }
            } else {
                setCloudApiTestStatus(prev => ({
                    ...prev,
                    [providerId]: { status: 'error', errorMsg: data.error || 'Invalid API Key' },
                }));
            }
        } catch (e) {
            setCloudApiTestStatus(prev => ({
                ...prev,
                [providerId]: { status: 'error', errorMsg: e instanceof Error ? e.message : 'Network error' },
            }));
        }
    }

    const [layaStatus, setLayaStatus] = useState<LayaStatus | null>(null);
    const [layaStatusLoading, setLayaStatusLoading] = useState(false);
    const layaModels = layaStatus?.lmstudio?.models ?? [];

    const fetchLayaStatus = useCallback(async () => {
        setLayaStatusLoading(true);
        try {
            const res = await fetch(
                `${API_BASE}/api/v1/laya/status`,
                withAdminAuth({ signal: AbortSignal.timeout(8000) })
            );
            if (res.ok) setLayaStatus(await res.json() as LayaStatus);
        } catch { /* backend unreachable — card shows form values only */ }
        setLayaStatusLoading(false);
    }, []);

    const checkLocalLLMs = useCallback(async () => {
        // Check Ollama
        try {
            const res = await fetch(`${settings.ollama_base_url}/api/tags`, { signal: AbortSignal.timeout(3000) });
            setOllamaStatus(res.ok ? 'online' : 'offline');
        } catch { setOllamaStatus('offline'); }

        // Check LM Studio
        try {
            const res = await fetch(`${settings.lmstudio_base_url}/models`, { signal: AbortSignal.timeout(3000) });
            setLmstudioStatus(res.ok ? 'online' : 'offline');
        } catch { setLmstudioStatus('offline'); }
    }, [settings.ollama_base_url, settings.lmstudio_base_url]);

    const loadSettings = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v1/settings`, withAdminAuth());
            if (res.ok) {
                const data = await res.json();
                setApiKeyConfigured({
                    gemini: Boolean(data.gemini_api_key_configured),
                    openai: Boolean(data.openai_api_key_configured),
                    openrouter: Boolean(data.openrouter_api_key_configured),
                    mistral: Boolean(data.mistral_api_key_configured),
                    vertex: Boolean(data.vertex_api_key_configured),
                    nvidia: Boolean(data.nvidia_api_key_configured),
                });
                setSettings(prev => ({ ...prev, ...data, laya: { ...prev.laya, ...(data.laya || {}) } }));
            }
        } catch { /* Backend may not be running yet */ }
    }, []);

    useEffect(() => {
        void checkLocalLLMs();
        void loadSettings();
        void fetchModels();
        void fetchLayaStatus();
    }, [checkLocalLLMs, loadSettings, fetchModels, fetchLayaStatus]);

    async function saveSettings() {
        setSaving(true);
        try {
            const res = await fetch(
                `${API_BASE}/api/v1/settings`,
                withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings),
                })
            );
            if (res.ok) {
                const result = await res.json() as { settings?: Record<string, unknown> };
                const resultSettings = result.settings || {};
                setApiKeyConfigured({
                    gemini: Boolean(resultSettings.gemini_api_key_configured),
                    openai: Boolean(resultSettings.openai_api_key_configured),
                    openrouter: Boolean(resultSettings.openrouter_api_key_configured),
                    mistral: Boolean(resultSettings.mistral_api_key_configured),
                    vertex: Boolean(resultSettings.vertex_api_key_configured),
                    nvidia: Boolean(resultSettings.nvidia_api_key_configured),
                });
                setSaveError('');
                setSaved(true);
                setTimeout(() => setSaved(false), 3000);
            } else {
                setSaveError(`Could not save settings (HTTP ${res.status}).`);
            }
        } catch (e) {
            setSaveError(e instanceof Error ? e.message : 'Could not save settings. Check the backend connection and try again.');
        }
        setSaving(false);
    }

    function updateField<K extends keyof SettingsState>(field: K, value: SettingsState[K]) {
        setSettings(prev => ({ ...prev, [field]: value }));
    }

    function updateRouting(agent: string, provider: string) {
        setSettings(prev => ({
            ...prev,
            agent_routing: { ...prev.agent_routing, [agent]: provider },
        }));
    }

    function updateAllRouting(provider: string) {
        setSettings(prev => {
            const nextRouting = { ...prev.agent_routing };
            AGENTS.forEach(ag => {
                nextRouting[ag.name] = provider;
            });
            return {
                ...prev,
                agent_routing: nextRouting,
                default_llm_provider: provider
            };
        });
    }

    function toggleShowKey(key: string) {
        setShowKeys(prev => ({ ...prev, [key]: !prev[key] }));
    }

    async function initializeMcp(providerId: string) {
        let key = mcpState[providerId]?.key;
        if (providerId === 'serper') key = settings.serper_api_key;
        if (providerId === 'searxng') key = settings.searxng_instance_url;
        if (providerId === 'fmp') key = settings.fmp_api_key;
        if (providerId === 'alpha_vantage') key = settings.alpha_vantage_api_key;
        if (providerId === 'financial_datasets') key = settings.financial_datasets_api_key;
        if (providerId === 'finnhub') key = settings.finnhub_api_key;
        if (providerId === 'sec_api') key = settings.sec_api_key;

        if (!key && providerId !== 'ddg') return;
        setMcpState(prev => ({ ...prev, [providerId]: { ...prev[providerId], status: 'testing' } }));
        try {
            const res = await fetch(
                `${API_BASE}/api/v1/mcp/initialize`,
                withAdminAuth({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerId, api_key: key }),
                })
            );
            const data = await res.json();
            if (data.ok) {
                setMcpState(prev => ({
                    ...prev,
                    [providerId]: { ...prev[providerId], status: 'connected', latency: data.latency_ms },
                }));
            } else {
                setMcpState(prev => ({
                    ...prev,
                    [providerId]: { ...prev[providerId], status: 'error', errorMsg: data.error || 'Unknown error' },
                }));
            }
        } catch (e) {
            setMcpState(prev => ({
                ...prev,
                [providerId]: { ...prev[providerId], status: 'error', errorMsg: e instanceof Error ? e.message : 'Request failed' },
            }));
        }
    }

    function StatusDot({ status }: { status: 'checking' | 'online' | 'offline' }) {
        if (status === 'checking') return <Loader2 className="h-4 w-4 animate-spin text-slate-400" />;
        if (status === 'online') return <CheckCircle className="h-4 w-4 text-green-500" />;
        return <XCircle className="h-4 w-4 text-red-400" />;
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-col space-y-2">
                <h2 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-violet-600 to-fuchsia-600 bg-clip-text text-transparent dark:from-violet-400 dark:to-fuchsia-400">
                    Settings
                </h2>
                <p className="text-muted-foreground">
                    Configure API keys, local LLMs, and model routing for your agents.
                </p>
            </div>

            {/* ===== API Usage Monitor ===== */}
            <ApiUsageMonitor />

            {/* ===== API Keys Section ===== */}
            <Card className="border-t-4 border-t-blue-500">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Key className="h-5 w-5 text-blue-500" />
                        Cloud API Keys
                    </CardTitle>
                    <CardDescription>Enter your API keys for cloud LLM providers.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {Object.entries(cloudApiTestStatus).filter(([, state]) => state.status === 'connected' && state.detail).map(([provider, state]) => (
                        <p key={provider} className="text-xs text-emerald-700 dark:text-emerald-400">
                            {provider}: {state.detail}
                        </p>
                    ))}
                    {/* Gemini */}
                    <div className="space-y-2">
                        <Label className="flex items-center gap-2">
                            <Cloud className="h-4 w-4 text-blue-500" /> Google Gemini API Key
                        </Label>
                        {apiKeyConfigured.gemini && <p className="text-xs text-emerald-600">A key is saved. Enter a new key to replace it.</p>}
                        {cloudApiTestStatus.gemini?.status === 'error' && cloudApiTestStatus.gemini?.errorMsg && (
                            <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                {cloudApiTestStatus.gemini.errorMsg}
                            </div>
                        )}
                        <div className="flex gap-2">
                            <div className="relative flex-1">
                                <Input
                                    id="gemini-key"
                                    type={showKeys['gemini'] ? 'text' : 'password'}
                                    value={settings.gemini_api_key}
                                    onChange={e => {
                                        updateField('gemini_api_key', e.target.value);
                                        setCloudApiTestStatus(prev => ({ ...prev, gemini: { status: 'idle' } }));
                                    }}
                                    placeholder="AIzaSy..."
                                    className="pr-10"
                                />
                                <button onClick={() => toggleShowKey('gemini')} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                                    {showKeys['gemini'] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                            <Button
                                size="sm"
                                variant={cloudApiTestStatus.gemini?.status === 'connected' ? 'outline' : 'default'}
                                onClick={() => testCloudApi('gemini')}
                                disabled={cloudApiTestStatus.gemini?.status === 'testing' || !settings.gemini_api_key}
                                className="shrink-0"
                            >
                                {cloudApiTestStatus.gemini?.status === 'testing' ? (
                                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                ) : cloudApiTestStatus.gemini?.status === 'connected' ? (
                                    <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                ) : (
                                    <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize & Test</>
                                )}
                            </Button>
                        </div>
                    </div>
                    <Separator />

                    {/* OpenAI */}
                    <div className="space-y-2">
                        <Label className="flex items-center gap-2">
                            <Cloud className="h-4 w-4 text-green-500" /> OpenAI API Key
                        </Label>
                        {apiKeyConfigured.openai && <p className="text-xs text-emerald-600">A key is saved. Enter a new key to replace it.</p>}
                        {cloudApiTestStatus.openai?.status === 'error' && cloudApiTestStatus.openai?.errorMsg && (
                            <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                {cloudApiTestStatus.openai.errorMsg}
                            </div>
                        )}
                        <div className="flex gap-2">
                            <div className="relative flex-1">
                                <Input
                                    id="openai-key"
                                    type={showKeys['openai'] ? 'text' : 'password'}
                                    value={settings.openai_api_key}
                                    onChange={e => {
                                        updateField('openai_api_key', e.target.value);
                                        setCloudApiTestStatus(prev => ({ ...prev, openai: { status: 'idle' } }));
                                    }}
                                    placeholder="sk-..."
                                    className="pr-10"
                                />
                                <button onClick={() => toggleShowKey('openai')} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                                    {showKeys['openai'] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                            <Button
                                size="sm"
                                variant={cloudApiTestStatus.openai?.status === 'connected' ? 'outline' : 'default'}
                                onClick={() => testCloudApi('openai')}
                                disabled={cloudApiTestStatus.openai?.status === 'testing' || !settings.openai_api_key}
                                className="shrink-0"
                            >
                                {cloudApiTestStatus.openai?.status === 'testing' ? (
                                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                ) : cloudApiTestStatus.openai?.status === 'connected' ? (
                                    <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                ) : (
                                    <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize & Test</>
                                )}
                            </Button>
                        </div>
                    </div>
                    <Separator />

                    {/* Mistral */}
                    <div className="space-y-2">
                        <Label className="flex items-center gap-2">
                            <Cloud className="h-4 w-4 text-orange-500" /> Mistral API Key
                        </Label>
                        {apiKeyConfigured.mistral && <p className="text-xs text-emerald-600">A key is saved. Enter a new key to replace it.</p>}
                        {cloudApiTestStatus.mistral?.status === 'error' && cloudApiTestStatus.mistral?.errorMsg && (
                            <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                {cloudApiTestStatus.mistral.errorMsg}
                            </div>
                        )}
                        <div className="flex gap-2">
                            <div className="relative flex-1">
                                <Input
                                    id="mistral-key"
                                    type={showKeys['mistral'] ? 'text' : 'password'}
                                    value={settings.mistral_api_key}
                                    onChange={e => {
                                        updateField('mistral_api_key', e.target.value);
                                        setCloudApiTestStatus(prev => ({ ...prev, mistral: { status: 'idle' } }));
                                    }}
                                    placeholder="..."
                                    className="pr-10"
                                />
                                <button onClick={() => toggleShowKey('mistral')} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                                    {showKeys['mistral'] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                            <Button
                                size="sm"
                                variant={cloudApiTestStatus.mistral?.status === 'connected' ? 'outline' : 'default'}
                                onClick={() => testCloudApi('mistral')}
                                disabled={cloudApiTestStatus.mistral?.status === 'testing' || !settings.mistral_api_key}
                                className="shrink-0"
                            >
                                {cloudApiTestStatus.mistral?.status === 'testing' ? (
                                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                ) : cloudApiTestStatus.mistral?.status === 'connected' ? (
                                    <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                ) : (
                                    <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize & Test</>
                                )}
                            </Button>
                        </div>
                    </div>
                    <Separator />

                    {/* OpenRouter */}
                    <div className="space-y-2">
                        <Label className="flex items-center gap-2">
                            <Cloud className="h-4 w-4 text-cyan-600" /> OpenRouter API Key
                        </Label>
                        {apiKeyConfigured.openrouter && <p className="text-xs text-emerald-600">A key is saved. Enter a new key to replace it.</p>}
                        <p className="text-xs text-muted-foreground">Use one OpenRouter key to access its model catalog. Model availability, pricing, and tool support vary by model.</p>
                        {cloudApiTestStatus.openrouter?.status === 'error' && cloudApiTestStatus.openrouter.errorMsg && (
                            <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                {cloudApiTestStatus.openrouter.errorMsg}
                            </div>
                        )}
                        <div className="flex gap-2">
                            <div className="relative flex-1">
                                <Input
                                    id="openrouter-key"
                                    type={showKeys.openrouter ? 'text' : 'password'}
                                    value={settings.openrouter_api_key}
                                    onChange={e => {
                                        updateField('openrouter_api_key', e.target.value);
                                        setCloudApiTestStatus(prev => ({ ...prev, openrouter: { status: 'idle' } }));
                                    }}
                                    placeholder="sk-or-v1-..."
                                    className="pr-10"
                                />
                                <button type="button" aria-label={showKeys.openrouter ? 'Hide OpenRouter API key' : 'Show OpenRouter API key'} onClick={() => toggleShowKey('openrouter')} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                                    {showKeys.openrouter ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                            <Button
                                size="sm"
                                variant={cloudApiTestStatus.openrouter?.status === 'connected' ? 'outline' : 'default'}
                                onClick={() => testCloudApi('openrouter')}
                                disabled={cloudApiTestStatus.openrouter?.status === 'testing' || !settings.openrouter_api_key}
                                className="shrink-0"
                            >
                                {cloudApiTestStatus.openrouter?.status === 'testing' ? (
                                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                ) : cloudApiTestStatus.openrouter?.status === 'connected' ? (
                                    <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                ) : (
                                    <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize &amp; Test</>
                                )}
                            </Button>
                        </div>
                    </div>
                    <Separator />

                    {/* Vertex AI */}
                    <div className="space-y-3">
                        <Label className="flex items-center gap-2">
                            <Cloud className="h-4 w-4 text-purple-600" /> Google Vertex AI
                        </Label>
                        {apiKeyConfigured.vertex && <p className="text-xs text-emerald-600">Credentials are saved. Enter a new key to replace them.</p>}
                        {cloudApiTestStatus.vertex?.status === 'error' && cloudApiTestStatus.vertex?.errorMsg && (
                            <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                {cloudApiTestStatus.vertex.errorMsg}
                            </div>
                        )}
                        <div className="rounded-md border border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-800 p-2.5 text-xs text-blue-800 dark:text-blue-300 space-y-1">
                            <p className="font-medium">Vertex AI Authentication</p>
                            <p>Enter your Vertex AI API Key (Standard keys start with AIza, while OAuth2 tokens start with AQ or ya29).</p>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="vertex-key" className="text-xs text-muted-foreground">
                                API Key or OAuth2 Token
                            </Label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Input
                                        id="vertex-key"
                                        type={showKeys['vertex'] ? 'text' : 'password'}
                                        value={settings.vertex_api_key}
                                        onChange={e => {
                                            updateField('vertex_api_key', e.target.value);
                                            setCloudApiTestStatus(prev => ({ ...prev, vertex: { status: 'idle' } }));
                                        }}
                                        placeholder="AIzaSy..."
                                        className="pr-10"
                                    />
                                    <button onClick={() => toggleShowKey('vertex')} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                                        {showKeys['vertex'] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                    </button>
                                </div>
                                <Button
                                    size="sm"
                                    variant={cloudApiTestStatus.vertex?.status === 'connected' ? 'outline' : 'default'}
                                    onClick={() => testCloudApi('vertex')}
                                    disabled={cloudApiTestStatus.vertex?.status === 'testing' || !settings.vertex_api_key}
                                    className="shrink-0"
                                >
                                    {cloudApiTestStatus.vertex?.status === 'testing' ? (
                                        <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                    ) : cloudApiTestStatus.vertex?.status === 'connected' ? (
                                        <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                    ) : (
                                        <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize & Test</>
                                    )}
                                </Button>
                            </div>
                        </div>
                        {/* Validation warning removed to support all valid key types */}
                        {/* GCP Project ID and Location */}
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label htmlFor="vertex-project-id" className="text-xs text-muted-foreground">
                                    GCP Project ID
                                </Label>
                                <Input
                                    id="vertex-project-id"
                                    value={settings.vertex_project_id}
                                    onChange={e => updateField('vertex_project_id', e.target.value)}
                                    placeholder="my-gcp-project"
                                    className="h-8 text-sm"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="vertex-location" className="text-xs text-muted-foreground">
                                    Region/Location
                                </Label>
                                <select
                                    id="vertex-location"
                                    value={settings.vertex_location}
                                    onChange={e => updateField('vertex_location', e.target.value)}
                                    className="h-8 w-full rounded-md border bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                >
                                    <option value="us-central1">us-central1 (Iowa)</option>
                                    <option value="us-east1">us-east1 (South Carolina)</option>
                                    <option value="us-west1">us-west1 (Oregon)</option>
                                    <option value="europe-west1">europe-west1 (Belgium)</option>
                                    <option value="europe-west4">europe-west4 (Netherlands)</option>
                                    <option value="asia-northeast1">asia-northeast1 (Tokyo)</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <Separator />

                    {/* NVIDIA, Anthropic, and Groq provider settings */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="nvidia-key" className="text-xs flex items-center gap-1.5">
                                <Cloud className="h-3.5 w-3.5 text-lime-500" /> NVIDIA API Key
                            </Label>
                            {apiKeyConfigured.nvidia && <p className="text-xs text-emerald-600">A key is saved. Enter a new key to replace it.</p>}
                            <Input
                                id="nvidia-key"
                                type={showKeys['nvidia'] ? 'text' : 'password'}
                                value={settings.nvidia_api_key}
                                onChange={e => {
                                    updateField('nvidia_api_key', e.target.value);
                                    setCloudApiTestStatus(prev => ({ ...prev, nvidia: { status: 'idle' } }));
                                }}
                                placeholder="nvapi-..."
                                className="h-8 text-sm"
                            />
                            <Label htmlFor="nvidia-base-url" className="text-xs flex items-center gap-1.5 pt-2">
                                NVIDIA API Base URL
                            </Label>
                            <Input
                                id="nvidia-base-url"
                                value={settings.nvidia_base_url}
                                onChange={e => updateField('nvidia_base_url', e.target.value)}
                                placeholder="https://integrate.api.nvidia.com/v1"
                                className="h-8 text-sm"
                            />
                            <Button
                                size="sm"
                                variant={cloudApiTestStatus.nvidia?.status === 'connected' ? 'outline' : 'default'}
                                onClick={() => testCloudApi('nvidia')}
                                disabled={cloudApiTestStatus.nvidia?.status === 'testing' || !settings.nvidia_api_key}
                                className="mt-2"
                            >
                                {cloudApiTestStatus.nvidia?.status === 'testing' ? (
                                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                ) : cloudApiTestStatus.nvidia?.status === 'connected' ? (
                                    <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Connected</>
                                ) : (
                                    <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize &amp; Test</>
                                )}
                            </Button>
                            {cloudApiTestStatus.nvidia?.status === 'error' && cloudApiTestStatus.nvidia.errorMsg && (
                                <p className="text-xs text-red-600">{cloudApiTestStatus.nvidia.errorMsg}</p>
                            )}
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="anthropic-key" className="text-xs flex items-center gap-1.5">
                                <Cloud className="h-3.5 w-3.5 text-amber-500" /> Anthropic API Key
                            </Label>
                            <Input
                                id="anthropic-key"
                                type={showKeys['anthropic'] ? 'text' : 'password'}
                                value={settings.anthropic_api_key}
                                onChange={e => updateField('anthropic_api_key', e.target.value)}
                                placeholder="sk-ant-..."
                                className="h-8 text-sm"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="groq-key" className="text-xs flex items-center gap-1.5">
                                <Cloud className="h-3.5 w-3.5 text-rose-500" /> Groq API Key
                            </Label>
                            <Input
                                id="groq-key"
                                type={showKeys['groq'] ? 'text' : 'password'}
                                value={settings.groq_api_key}
                                onChange={e => updateField('groq_api_key', e.target.value)}
                                placeholder="gsk_..."
                                className="h-8 text-sm"
                            />
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ===== External API Providers Section ===== */}
            <Card className="border-t-4 border-t-teal-500">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Database className="h-5 w-5 text-teal-500" />
                        External API Integrations
                    </CardTitle>
                    <CardDescription>
                        Credentialed finance and research APIs used by compatible tools. These are API integrations,
                        not MCP protocol servers; availability and retrieved evidence are checked during analysis.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {MCP_PROVIDERS_CONFIG.map(provider => {
                        const cfg = mcpState[provider.id];
                        const status = cfg?.status ?? 'idle';
                        return (
                            <div key={provider.id} className="rounded-lg border p-4 space-y-3">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <span className="text-xl">{provider.icon}</span>
                                        <div>
                                            <p className="font-semibold text-sm">{provider.name}</p>
                                            <p className="text-xs text-muted-foreground">{provider.description}</p>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {status === 'connected' && (
                                            <Badge className="bg-green-100 text-green-700 border-green-300 gap-1">
                                                <CheckCircle className="h-3 w-3" /> Connected
                                                {cfg.latency ? ` (${cfg.latency}ms)` : ''}
                                            </Badge>
                                        )}
                                        {status === 'error' && (
                                            <Badge variant="destructive" className="gap-1">
                                                <XCircle className="h-3 w-3" /> Error
                                            </Badge>
                                        )}
                                        {status === 'idle' && (
                                            <Badge variant="outline" className="text-slate-500">Not initialized</Badge>
                                        )}
                                        {status === 'testing' && (
                                            <Badge variant="outline" className="gap-1">
                                                <Loader2 className="h-3 w-3 animate-spin" /> Testing...
                                            </Badge>
                                        )}
                                    </div>
                                </div>

                                <div className="flex flex-wrap gap-1">
                                    {provider.capabilities.map(cap => (
                                        <Badge key={cap} variant="secondary" className="text-[10px] px-1.5 py-0">
                                            <Zap className="h-2.5 w-2.5 mr-1" />{cap}
                                        </Badge>
                                    ))}
                                </div>

                                {status === 'error' && cfg.errorMsg && (
                                    <div className="flex items-start gap-1.5 rounded-md bg-red-50 dark:bg-red-950/20 p-2 text-xs text-red-600">
                                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                        {cfg.errorMsg}
                                    </div>
                                )}

                                <div className="flex gap-2">
                                    <div className="relative flex-1">
                                        <Input
                                            id={`mcp-key-${provider.id}`}
                                            type={showKeys[`mcp-${provider.id}`] ? 'text' : 'password'}
                                            value={
                                                provider.id === 'serper' ? settings.serper_api_key :
                                                    provider.id === 'searxng' ? settings.searxng_instance_url :
                                                        provider.id === 'fmp' ? settings.fmp_api_key :
                                                            provider.id === 'alpha_vantage' ? settings.alpha_vantage_api_key :
                                                                provider.id === 'financial_datasets' ? settings.financial_datasets_api_key :
                                                                    provider.id === 'finnhub' ? settings.finnhub_api_key :
                                                                        provider.id === 'sec_api' ? settings.sec_api_key :
                                                                            cfg?.key ?? ''
                                            }
                                            onChange={e => {
                                                const val = e.target.value;
                                                if (provider.id === 'serper') updateField('serper_api_key', val);
                                                else if (provider.id === 'searxng') updateField('searxng_instance_url', val);
                                                else if (provider.id === 'fmp') updateField('fmp_api_key', val);
                                                else if (provider.id === 'alpha_vantage') updateField('alpha_vantage_api_key', val);
                                                else if (provider.id === 'financial_datasets') updateField('financial_datasets_api_key', val);
                                                else if (provider.id === 'finnhub') updateField('finnhub_api_key', val);
                                                else if (provider.id === 'sec_api') updateField('sec_api_key', val);

                                                setMcpState(prev => ({
                                                    ...prev,
                                                    [provider.id]: { ...prev[provider.id], key: val, status: 'idle' },
                                                }));
                                            }}
                                            placeholder={
                                                provider.id === 'searxng' ? 'Instance URL (e.g. http://localhost:8080)' :
                                                    `${provider.name} API Key`
                                            }
                                            className="pr-10 font-mono text-xs"
                                        />
                                        <button
                                            onClick={() => toggleShowKey(`mcp-${provider.id}`)}
                                            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                        >
                                            {showKeys[`mcp-${provider.id}`] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                        </button>
                                    </div>
                                    <Button
                                        size="sm"
                                        variant={status === 'connected' ? 'outline' : 'default'}
                                        onClick={() => initializeMcp(provider.id)}
                                        disabled={status === 'testing' || !mcpState[provider.id]?.key}
                                        className="shrink-0"
                                    >
                                        {status === 'testing' ? (
                                            <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />Testing...</>
                                        ) : status === 'connected' ? (
                                            <><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Re-test</>
                                        ) : (
                                            <><Zap className="h-3.5 w-3.5 mr-1.5" />Initialize & Test</>
                                        )}
                                    </Button>
                                </div>
                            </div>
                        );
                    })}
                </CardContent>
            </Card>

            {/* ===== Web Search Strategy Section ===== */}
            <Card className="border-t-4 border-t-orange-500 overflow-hidden">
                <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2">
                        <RefreshCw className="h-5 w-5 text-orange-500" />
                        Search Fallback Strategy
                    </CardTitle>
                    <CardDescription>
                        Define the priority order for web search. If the primary provider fails,
                        the agents will automatically fallback to the next available tool.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-col gap-2">
                        {settings.search_priority.map((id, index) => {
                            const provider = MCP_PROVIDERS_CONFIG.find(p => p.id === id);
                            if (!provider) return null;
                            return (
                                <div
                                    key={id}
                                    className="flex items-center justify-between p-3 rounded-md border bg-slate-50/50 dark:bg-slate-900/50 hover:border-orange-200 transition-colors group"
                                >
                                    <div className="flex items-center gap-3">
                                        <div className="flex items-center justify-center w-6 h-6 rounded bg-orange-100 text-orange-700 text-xs font-bold">
                                            {index + 1}
                                        </div>
                                        <span className="text-xl">{provider.icon}</span>
                                        <div>
                                            <p className="font-medium text-sm">{provider.name}</p>
                                            <p className="text-[10px] text-muted-foreground uppercase tracking-tight">
                                                {index === 0 ? 'Primary' : `Fallback ${index}`}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                        <Button
                                            size="icon"
                                            variant="ghost"
                                            className="h-7 w-7"
                                            disabled={index === 0}
                                            onClick={() => {
                                                const next = [...settings.search_priority];
                                                [next[index], next[index - 1]] = [next[index - 1], next[index]];
                                                updateField('search_priority', next);
                                            }}
                                        >
                                            <Zap className="h-3 w-3 rotate-180" />
                                        </Button>
                                        <Button
                                            size="icon"
                                            variant="ghost"
                                            className="h-7 w-7"
                                            disabled={index === settings.search_priority.length - 1}
                                            onClick={() => {
                                                const next = [...settings.search_priority];
                                                [next[index], next[index + 1]] = [next[index + 1], next[index]];
                                                updateField('search_priority', next);
                                            }}
                                        >
                                            <Zap className="h-3 w-3" />
                                        </Button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    <div className="flex items-center gap-2 p-3 bg-amber-50 dark:bg-amber-950/20 border border-amber-100 dark:border-amber-900/30 rounded-md text-[11px] text-amber-700 dark:text-amber-400">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        <span>
                            <strong>Note:</strong> DuckDuckGo (DDG) serves as the "Iron-Clad Fallback" since it
                            requires no API key. We recommend keeping it in the list to ensure 24/7 reliability.
                        </span>
                    </div>
                </CardContent>
            </Card>

            {/* ===== Cloud Model Selection ===== */}
            <Card className="border-t-4 border-t-cyan-500">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Cloud className="h-5 w-5 text-cyan-500" />
                                Cloud Model Selection
                            </CardTitle>
                            <CardDescription>Models are fetched live from each provider's API. Click Refresh to update.</CardDescription>
                        </div>
                        <Button variant="outline" size="sm" onClick={fetchModels} disabled={fetchingModels} className="gap-1.5">
                            <RefreshCw className={`h-3.5 w-3.5 ${fetchingModels ? 'animate-spin' : ''}`} /> Refresh Models
                        </Button>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-3 gap-4">
                        {/* Gemini */}
                        <div className="space-y-1">
                            <Label htmlFor="gemini-model" className="text-xs flex items-center gap-1">
                                Gemini Model
                                {availableModels.gemini && (
                                    <Badge variant="outline" className="text-[9px] px-1">
                                        {availableModels.gemini.models.length} available
                                    </Badge>
                                )}
                            </Label>
                            <select
                                id="gemini-model"
                                value={settings.gemini_model}
                                onChange={e => updateField('gemini_model', e.target.value)}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                {availableModels.gemini?.models?.length ? (
                                    availableModels.gemini.models.map(m => (
                                        <option key={m.id} value={m.id}>
                                            {m.name || m.id}{m.context_window ? ` · ${Math.round(m.context_window / 1000)}K ctx` : ''}{m.daily_limit ? ` · ${m.daily_limit}` : ''}
                                        </option>
                                    ))
                                ) : fetchingModels ? (
                                    <option value="">Loading models...</option>
                                ) : availableModels.gemini?.status === 'no_key' ? (
                                    <option value="">Set Gemini API key first</option>
                                ) : (
                                    <option value="">Click Refresh Models</option>
                                )}
                            </select>
                        </div>
                        {/* OpenAI */}
                        <div className="space-y-1">
                            <Label htmlFor="openai-model" className="text-xs flex items-center gap-1">
                                OpenAI Model
                                {availableModels.openai && (
                                    <Badge variant="outline" className="text-[9px] px-1">
                                        {availableModels.openai.models.length} available
                                    </Badge>
                                )}
                            </Label>
                            <select
                                id="openai-model"
                                value={settings.openai_model}
                                onChange={e => updateField('openai_model', e.target.value)}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                {availableModels.openai?.models?.length ? (
                                    availableModels.openai.models.map(m => (
                                        <option key={m.id} value={m.id}>
                                            {m.name || m.id}{m.context_window ? ` · ${Math.round(m.context_window / 1000)}K ctx` : ''}{m.daily_limit ? ` · ${m.daily_limit}` : ''}
                                        </option>
                                    ))
                                ) : fetchingModels ? (
                                    <option value="">Loading models...</option>
                                ) : availableModels.openai?.status === 'no_key' ? (
                                    <option value="">Set OpenAI API key first</option>
                                ) : (
                                    <option value="">Click Refresh Models</option>
                                )}
                            </select>
                        </div>
                        {/* OpenRouter */}
                        <div className="space-y-1">
                            <Label htmlFor="openrouter-model" className="text-xs flex items-center gap-1">
                                OpenRouter Model
                                {availableModels.openrouter && (
                                    <Badge variant="outline" className="text-[9px] px-1">
                                        {availableModels.openrouter.models.length} available
                                    </Badge>
                                )}
                            </Label>
                            <select
                                id="openrouter-model"
                                value={settings.openrouter_model}
                                onChange={e => updateField('openrouter_model', e.target.value)}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                {availableModels.openrouter?.models?.length ? (
                                    availableModels.openrouter.models.map(m => (
                                        <option key={m.id} value={m.id}>
                                            {m.name || m.id}{m.context_window ? ` · ${Math.round(m.context_window / 1000)}K ctx` : ''}
                                        </option>
                                    ))
                                ) : (
                                    <option value={settings.openrouter_model}>{settings.openrouter_api_key ? 'Click Refresh Models' : 'Set OpenRouter API key first'}</option>
                                )}
                            </select>
                        </div>
                        {/* Mistral */}
                        <div className="space-y-1">
                            <Label htmlFor="mistral-model" className="text-xs flex items-center gap-1">
                                Mistral Model
                                {availableModels.mistral && (
                                    <Badge variant="outline" className="text-[9px] px-1">
                                        {availableModels.mistral.models.length} available
                                    </Badge>
                                )}
                            </Label>
                            <select
                                id="mistral-model"
                                value={settings.mistral_model}
                                onChange={e => updateField('mistral_model', e.target.value)}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                {availableModels.mistral?.models?.length ? (
                                    availableModels.mistral.models.map(m => (
                                        <option key={m.id} value={m.id}>
                                            {m.name || m.id}{m.context_window ? ` · ${Math.round(m.context_window / 1000)}K ctx` : ''}{m.daily_limit ? ` · ${m.daily_limit}` : ''}
                                        </option>
                                    ))
                                ) : fetchingModels ? (
                                    <option value="">Loading models...</option>
                                ) : availableModels.mistral?.status === 'no_key' ? (
                                    <option value="">Set Mistral API key first</option>
                                ) : (
                                    <option value="">Click Refresh Models</option>
                                )}
                            </select>
                        </div>

                        {/* NVIDIA NIM */}
                        <div className="space-y-1">
                            <Label htmlFor="nvidia-model" className="text-xs flex items-center gap-1">
                                NVIDIA NIM Model
                                {availableModels.nvidia && (
                                    <Badge variant="outline" className="text-[9px] px-1">
                                        {availableModels.nvidia.models.length} available
                                    </Badge>
                                )}
                            </Label>
                            <select
                                id="nvidia-model"
                                value={settings.nvidia_model}
                                onChange={e => updateField('nvidia_model', e.target.value)}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                {availableModels.nvidia?.models?.length ? (
                                    availableModels.nvidia.models.map(m => (
                                        <option key={m.id} value={m.id}>{m.name || m.id}</option>
                                    ))
                                ) : (
                                    <option value={settings.nvidia_model}>{settings.nvidia_api_key ? 'Click Refresh Models' : 'Set NVIDIA API key first'}</option>
                                )}
                            </select>
                        </div>

                         {/* Vertex AI */}
                         <div className="space-y-1">
                             <Label htmlFor="vertex-model" className="text-xs flex items-center gap-1">
                                 Vertex AI Model
                             </Label>
                             <select
                                 id="vertex-model"
                                 value={settings.vertex_model}
                                 onChange={e => updateField('vertex_model', e.target.value)}
                                 className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                             >
                                 {availableModels.vertex?.models?.length ? (
                                     availableModels.vertex.models.map(m => (
                                         <option key={m.id} value={m.id}>
                                             {m.name || m.id}
                                         </option>
                                     ))
                                 ) : (
                                     <>
                                         <option value="gemini-3.8-flash">gemini-3.8-flash (Recommended)</option>
                                         <option value="gemini-3.7-flash">gemini-3.7-flash</option>
                                         <option value="gemini-3.6-flash">gemini-3.6-flash</option>
                                         <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite</option>
                                         <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
                                         <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                                         <option value="gemini-2.5-flash">gemini-2.5-flash</option>
                                         <option value="gemini-2.5-pro">gemini-2.5-pro</option>
                                     </>
                                 )}
                             </select>
                         </div>
                     </div>
                 </CardContent>
            </Card>

            {/* ===== Local LLMs Section ===== */}
            <Card className="border-t-4 border-t-purple-500">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Cpu className="h-5 w-5 text-purple-500" />
                        Local LLMs
                    </CardTitle>
                    <CardDescription>Configure Ollama and LM Studio for local inference on your GPU.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    {/* Ollama */}
                    <div className="rounded-lg border p-4 space-y-3">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <h4 className="font-semibold">Ollama</h4>
                                <StatusDot status={ollamaStatus} />
                                <span className="text-xs text-muted-foreground">
                                    {ollamaStatus === 'online' ? `Running (${availableModels.ollama?.models?.length || 0} models)` : ollamaStatus === 'offline' ? 'Not detected' : 'Checking...'}
                                </span>
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => { checkLocalLLMs(); fetchModels(); }}>Refresh</Button>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1">
                                <Label htmlFor="ollama-url" className="text-xs">Base URL</Label>
                                <Input id="ollama-url" value={settings.ollama_base_url} onChange={e => updateField('ollama_base_url', e.target.value)} placeholder="http://localhost:11434" />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="ollama-model" className="text-xs">Model</Label>
                                {availableModels.ollama?.models?.length ? (
                                    <select
                                        id="ollama-model"
                                        value={settings.ollama_model}
                                        onChange={e => updateField('ollama_model', e.target.value)}
                                        className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                    >
                                        {availableModels.ollama.models.map(m => (
                                            <option key={m.id} value={m.id}>
                                                {m.name}{m.parameter_size ? ` (${m.parameter_size})` : ''}
                                            </option>
                                        ))}
                                    </select>
                                ) : (
                                    <Input id="ollama-model" value={settings.ollama_model} onChange={e => updateField('ollama_model', e.target.value)} placeholder="llama3" />
                                )}
                            </div>
                        </div>
                    </div>

                    {/* LM Studio */}
                    <div className="rounded-lg border p-4 space-y-3">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <h4 className="font-semibold">LM Studio</h4>
                                <StatusDot status={lmstudioStatus} />
                                <span className="text-xs text-muted-foreground">
                                    {lmstudioStatus === 'online' ? `Running (${availableModels.lmstudio?.models?.length || 0} models)` : lmstudioStatus === 'offline' ? 'Not detected' : 'Checking...'}
                                </span>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1">
                                <Label htmlFor="lms-url" className="text-xs">Base URL</Label>
                                <Input id="lms-url" value={settings.lmstudio_base_url} onChange={e => updateField('lmstudio_base_url', e.target.value)} placeholder="http://localhost:1234/v1" />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="lms-model" className="text-xs">Model</Label>
                                {availableModels.lmstudio?.models?.length ? (
                                    <select
                                        id="lms-model"
                                        value={settings.lmstudio_model}
                                        onChange={e => updateField('lmstudio_model', e.target.value)}
                                        className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                    >
                                        {availableModels.lmstudio.models.map(m => (
                                            <option key={m.id} value={m.id}>{m.name || m.id}</option>
                                        ))}
                                    </select>
                                ) : (
                                    <Input id="lms-model" value={settings.lmstudio_model} onChange={e => updateField('lmstudio_model', e.target.value)} placeholder="local-model" />
                                )}
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ===== Model Routing Section ===== */}
            <Card className="border-t-4 border-t-amber-500">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Brain className="h-5 w-5 text-amber-500" />
                                Agent → LLM Provider Routing
                            </CardTitle>
                            <CardDescription>
                                Set each agent’s preferred provider. Laya classifies each task and can override its model by complexity tier.
                            </CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-muted-foreground whitespace-nowrap">Set all to:</span>
                            <select
                                onChange={e => {
                                    if (e.target.value) {
                                        updateAllRouting(e.target.value);
                                        e.target.value = ""; // reset dropdown
                                    }
                                }}
                                className="h-8 rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                defaultValue=""
                            >
                                <option value="" disabled>Select provider...</option>
                                {PROVIDER_OPTIONS.map(opt => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                            </select>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="space-y-3">
                        {AGENTS.map(agent => (
                            <div key={agent.name} className="flex items-center justify-between rounded-lg border p-3 hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                                <div className="flex-1">
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium text-sm">{agent.label}</span>
                                        <Badge variant={agent.complexity === 'high' ? 'default' : 'secondary'} className="text-[10px] px-1.5 py-0">
                                            {agent.complexity === 'high' ? '🧠 Complex' : '⚡ Light'}
                                        </Badge>
                                    </div>
                                    <span className="text-xs text-muted-foreground">{agent.desc}</span>
                                    {agent.recommendedModel && (
                                        <div className="mt-1">
                                            <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20 font-medium">
                                                ✨ Recommended: {agent.recommendedModel}
                                            </span>
                                        </div>
                                    )}
                                </div>
                                <select
                                    value={settings.agent_routing[agent.name] || 'gemini'}
                                    onChange={e => updateRouting(agent.name, e.target.value)}
                                    className="h-8 rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                >
                                    {PROVIDER_OPTIONS.map(opt => (
                                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                                    ))}
                                </select>
                            </div>
                        ))}
                    </div>
                    <Separator className="my-5" />
                    <div className="space-y-2">
                        <p className="text-sm font-medium">Laya model selection by task complexity</p>
                        <p className="text-xs text-muted-foreground">Set an optional exact model as provider:model. Laya applies it only when its task routing selects that provider; blank uses the provider’s default model.</p>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            {([['fast_model', 'Fast / simple'], ['general_model', 'General'], ['reasoning_model', 'Reasoning / complex']] as const).map(([key, label]) => (
                                <div className="space-y-1" key={key}>
                                    <Label htmlFor={`laya-${key}`} className="text-xs">{label}</Label>
                                    <Input id={`laya-${key}`} value={settings.laya[key]} onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, [key]: e.target.value } }))} placeholder="e.g. gemini:gemini-3.8-flash" />
                                </div>
                            ))}
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ===== PageIndex RAG Section ===== */}
            <Card className="border-t-4 border-t-emerald-500">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Shield className="h-5 w-5 text-emerald-500" />
                        PageIndex RAG
                    </CardTitle>
                    <CardDescription>Knowledge base and document indexing mode.</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-between rounded-lg border p-4">
                        <div>
                            <p className="font-medium text-sm">Self-Hosted Mode</p>
                            <p className="text-xs text-muted-foreground">Store indexes locally — no cloud dependency, better privacy.</p>
                        </div>
                        <Switch
                            id="pageindex-local"
                            checked={settings.pageindex_mode === 'local'}
                            onCheckedChange={checked => updateField('pageindex_mode', checked ? 'local' : 'cloud')}
                        />
                    </div>
                </CardContent>
            </Card>

            {/* ===== Laya System-1 + RAG Tuning ===== */}
            <Card className="border-t-4 border-t-violet-500">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Zap className="h-5 w-5 text-violet-500" />
                                Laya System-1 + RAG Tuning
                                {layaStatus && (
                                    <Badge
                                        variant="outline"
                                        className={`text-[10px] ${layaStatus.backend !== 'off' && layaStatus.backend !== 'heuristic'
                                            ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                                            : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'}`}
                                    >
                                        {layaStatus.backend === 'off' ? 'disabled'
                                            : layaStatus.backend === 'heuristic' ? 'fallback (no engine)'
                                            : `live: ${layaStatus.backend}`}
                                    </Badge>
                                )}
                            </CardTitle>
                            <CardDescription>
                                Fast decisions for routing, gating and rerank — plus hybrid RAG weights.
                                Backends: in-process checkpoints, <code className="font-mono">laya-serve</code>, or your LM Studio model.
                                Everything is fail-soft.
                            </CardDescription>
                        </div>
                        <Button variant="outline" size="sm" onClick={fetchLayaStatus} disabled={layaStatusLoading} className="gap-1.5 shrink-0">
                            <RefreshCw className={`h-3.5 w-3.5 ${layaStatusLoading ? 'animate-spin' : ''}`} />
                            Status
                        </Button>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    {layaStatus && (
                        <div className="rounded-lg border bg-muted/20 p-3 text-xs space-y-1.5">
                            <div className="flex items-center justify-between">
                                <span className="text-muted-foreground">Mode / backend</span>
                                <span className="font-mono font-medium">{layaStatus.mode} → {layaStatus.backend}</span>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-muted-foreground shrink-0">Decision model</span>
                                <span className="font-mono font-medium truncate" title={layaStatus.backend === 'lmstudio' ? layaStatus.lmstudio?.model : layaStatus.backend === 'remote' ? layaStatus.remote?.url : 'laya checkpoints'}>
                                    {layaStatus.backend === 'lmstudio'
                                        ? (layaStatus.lmstudio?.model || '—')
                                        : layaStatus.backend === 'remote'
                                            ? (layaStatus.remote?.url || '—')
                                            : layaStatus.backend === 'local'
                                                ? (layaStatus.local?.installed ? 'laya checkpoints (installed)' : 'not installed')
                                                : '—'}
                                </span>
                            </div>
                            {layaStatus.backend === 'lmstudio' && layaStatus.lmstudio?.model_source && (
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-muted-foreground shrink-0">Selected by</span>
                                    <span className="font-mono">{layaStatus.lmstudio.model_source.replaceAll('_', ' ')}</span>
                                </div>
                            )}
                            {layaStatus.backend === 'lmstudio' && (
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-muted-foreground shrink-0">LM Studio</span>
                                    {layaStatus.lmstudio?.reachable ? (
                                        <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium">
                                            <CheckCircle className="h-3.5 w-3.5" />
                                            online · {layaStatus.lmstudio.models?.length || 0} models
                                            {layaModels.length > 0 && (
                                                <span className="text-muted-foreground font-normal truncate max-w-[220px]" title={layaModels.join(', ')}>
                                                    ({layaModels.slice(0, 3).join(', ')}{layaModels.length > 3 ? ', …' : ''})
                                                </span>
                                            )}
                                        </span>
                                    ) : (
                                        <span className="flex items-center gap-1.5 text-red-500 font-medium">
                                            <XCircle className="h-3.5 w-3.5" />
                                            unreachable at {layaStatus.lmstudio?.base_url}
                                        </span>
                                    )}
                                </div>
                            )}
                            {layaStatus.backend === 'lmstudio' && layaStatus.lmstudio?.reachable &&
                                layaStatus.lmstudio?.model && !layaStatus.lmstudio.loaded_models?.includes(layaStatus.lmstudio.model) && (
                                    <div className="flex items-start gap-1.5 rounded-md bg-amber-500/10 border border-amber-500/30 p-2 text-amber-600 dark:text-amber-400">
                                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                        Model "{layaStatus.lmstudio.model}" is not loaded in LM Studio; Laya may not be able to generate decisions until it is loaded.
                                    </div>
                                )}
                        </div>
                    )}
                    <div className="flex items-center justify-between rounded-lg border p-4">
                        <div>
                            <p className="font-medium text-sm">Laya decision layer</p>
                            <p className="text-xs text-muted-foreground">Tier routing, confidence pre-gate, tool shortlist, RAG rerank.</p>
                        </div>
                        <Switch
                            id="laya-enabled"
                            checked={settings.laya.enabled}
                            onCheckedChange={checked => setSettings(prev => ({ ...prev, laya: { ...prev.laya, enabled: checked } }))}
                        />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="laya-mode" className="text-xs">Mode</Label>
                            <select
                                id="laya-mode"
                                value={settings.laya.mode}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, mode: e.target.value } }))}
                                className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                            >
                                <option value="auto">auto (local → remote → off)</option>
                                <option value="local">local (in-process)</option>
                                <option value="remote">remote (laya-serve)</option>
                                <option value="lmstudio">LM Studio (local chat model)</option>
                                <option value="off">off</option>
                            </select>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="laya-url" className="text-xs">Remote base URL</Label>
                            <Input
                                id="laya-url"
                                value={settings.laya.base_url}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, base_url: e.target.value } }))}
                                placeholder="http://laya-service:8000"
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="laya-lms-url" className="text-xs">LM Studio URL (lmstudio mode)</Label>
                            <Input
                                id="laya-lms-url"
                                value={settings.laya.lmstudio_url}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, lmstudio_url: e.target.value } }))}
                                placeholder="Blank = use Local LLM URL above"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="laya-lms-model" className="text-xs">LM Studio model override (optional)</Label>
                            <Input
                                id="laya-lms-model"
                                value={settings.laya.lmstudio_model}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, lmstudio_model: e.target.value } }))}
                                placeholder="Blank = follow the selected LM Studio model"
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="laya-fast" className="text-xs">Fast pool (simple)</Label>
                            <Input
                                id="laya-fast"
                                value={settings.laya.fast_pool}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, fast_pool: e.target.value } }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="laya-general" className="text-xs">General pool</Label>
                            <Input
                                id="laya-general"
                                value={settings.laya.general_pool}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, general_pool: e.target.value } }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="laya-reasoning" className="text-xs">Reasoning pool (complex)</Label>
                            <Input
                                id="laya-reasoning"
                                value={settings.laya.reasoning_pool}
                                onChange={e => setSettings(prev => ({ ...prev, laya: { ...prev.laya, reasoning_pool: e.target.value } }))}
                            />
                        </div>
                    </div>
                    <Separator />
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <span className="text-xs font-medium">Dense embeddings</span>
                            <Switch
                                checked={settings.rag.dense}
                                onCheckedChange={checked => setSettings(prev => ({ ...prev, rag: { ...prev.rag, dense: checked } }))}
                            />
                        </div>
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <span className="text-xs font-medium">Laya rerank</span>
                            <Switch
                                checked={settings.rag.laya_rerank}
                                onCheckedChange={checked => setSettings(prev => ({ ...prev, rag: { ...prev.rag, laya_rerank: checked } }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="rag-rerank-n" className="text-xs">Rerank depth (N)</Label>
                            <Input
                                id="rag-rerank-n"
                                type="number"
                                min={5}
                                max={50}
                                value={settings.rag.rerank_n}
                                onChange={e => setSettings(prev => ({ ...prev, rag: { ...prev.rag, rerank_n: parseInt(e.target.value) || 30 } }))}
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="rag-w-tree" className="text-xs">Weight: tree ({settings.rag.w_tree})</Label>
                            <input
                                id="rag-w-tree"
                                type="range"
                                min={0}
                                max={1}
                                step={0.05}
                                value={settings.rag.w_tree}
                                onChange={e => setSettings(prev => ({ ...prev, rag: { ...prev.rag, w_tree: parseFloat(e.target.value) } }))}
                                className="w-full"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="rag-w-bm25" className="text-xs">Weight: BM25 ({settings.rag.w_bm25})</Label>
                            <input
                                id="rag-w-bm25"
                                type="range"
                                min={0}
                                max={1}
                                step={0.05}
                                value={settings.rag.w_bm25}
                                onChange={e => setSettings(prev => ({ ...prev, rag: { ...prev.rag, w_bm25: parseFloat(e.target.value) } }))}
                                className="w-full"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="rag-w-dense" className="text-xs">Weight: dense ({settings.rag.w_dense})</Label>
                            <input
                                id="rag-w-dense"
                                type="range"
                                min={0}
                                max={1}
                                step={0.05}
                                value={settings.rag.w_dense}
                                onChange={e => setSettings(prev => ({ ...prev, rag: { ...prev.rag, w_dense: parseFloat(e.target.value) } }))}
                                className="w-full"
                            />
                        </div>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                        Saved with the rest of Settings. The backend picks these up from the settings store when present
                        (env vars <code className="font-mono">LAYA_*</code> / <code className="font-mono">RAG_*</code> remain the default).
                    </p>
                </CardContent>
            </Card>

            {/* ===== Document Design & Branding ===== */}
            <Card className="border-t-4 border-t-pink-500">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Palette className="h-5 w-5 text-pink-500" />
                        Document Design & Branding
                    </CardTitle>
                    <CardDescription>
                        Configure the default visual styling and layout for generated PDF and PPTX reports.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div className="grid grid-cols-2 gap-6">
                        {/* Branding */}
                        <div className="space-y-4">
                            <h4 className="font-medium text-sm border-b pb-1">Tenant Branding</h4>
                            <div className="space-y-3">
                                <div>
                                    <Label className="text-xs">Primary Color (Hex)</Label>
                                    <div className="flex gap-2 mt-1">
                                        <div
                                            className="w-8 h-9 rounded border shrink-0"
                                            style={{ backgroundColor: settings.branding?.primary_color || '#003366' }}
                                        />
                                        <Input
                                            value={settings.branding?.primary_color || ''}
                                            onChange={e => setSettings(prev => ({ ...prev, branding: { ...prev.branding, primary_color: e.target.value } }))}
                                            placeholder="#003366"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <Label className="text-xs">Secondary Color (Hex)</Label>
                                    <div className="flex gap-2 mt-1">
                                        <div
                                            className="w-8 h-9 rounded border shrink-0"
                                            style={{ backgroundColor: settings.branding?.secondary_color || '#E0E7FF' }}
                                        />
                                        <Input
                                            value={settings.branding?.secondary_color || ''}
                                            onChange={e => setSettings(prev => ({ ...prev, branding: { ...prev.branding, secondary_color: e.target.value } }))}
                                            placeholder="#E0E7FF"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <Label className="text-xs">Font Family</Label>
                                    <select
                                        value={settings.branding?.font_family || 'Helvetica'}
                                        onChange={e => setSettings(prev => ({ ...prev, branding: { ...prev.branding, font_family: e.target.value } }))}
                                        className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                    >
                                        <option value="Helvetica">Helvetica</option>
                                        <option value="Arial">Arial</option>
                                        <option value="Times-Roman">Times New Roman</option>
                                        <option value="Courier">Courier</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        {/* Layout Preferences */}
                        <div className="space-y-4">
                            <h4 className="flex items-center gap-2 font-medium text-sm border-b pb-1">
                                <FileText className="h-4 w-4" /> Default Report Layout
                            </h4>
                            <div className="space-y-3">
                                <div className="p-3 bg-slate-50 dark:bg-slate-900 rounded-md border">
                                    <p className="text-xs text-muted-foreground mb-3">
                                        The <strong>Report Architect</strong> agent uses these settings to dynamically structure the document sections and pick relevant charts (e.g. EBITDA Waterfalls for PE vs. Cohort Retention for VC).
                                    </p>

                                    <div className="space-y-3">
                                        <div>
                                            <Label className="text-xs">Target Audience</Label>
                                            <select
                                                value={settings.report_preferences?.target_audience || 'Investment Committee'}
                                                onChange={e => setSettings(prev => ({ ...prev, report_preferences: { ...prev.report_preferences, target_audience: e.target.value } }))}
                                                className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                            >
                                                <option value="Investment Committee">Investment Committee (IC)</option>
                                                <option value="Board of Directors">Board of Directors</option>
                                                <option value="Limited Partners">Limited Partners (LP)</option>
                                                <option value="Executive Management">Executive Management</option>
                                                <option value="Retail Investors">Retail Investors</option>
                                            </select>
                                        </div>
                                        <div>
                                            <Label className="text-xs">Industry Focus (Tone & Terminology)</Label>
                                            <select
                                                value={settings.report_preferences?.industry_focus || 'General'}
                                                onChange={e => setSettings(prev => ({ ...prev, report_preferences: { ...prev.report_preferences, industry_focus: e.target.value } }))}
                                                className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                                            >
                                                <option value="General">Generalist / Agnostic</option>
                                                <option value="Enterprise SaaS">Enterprise SaaS</option>
                                                <option value="Energy & Infrastructure">Energy & Infrastructure</option>
                                                <option value="Consumer & Retail">Consumer & Retail</option>
                                                <option value="Healthcare">Healthcare</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ===== LLM Gateway Cost Controls ===== */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">⚡ LLM Gateway — Cost Control</CardTitle>
                    <CardDescription>Token budgeting, rate limits, caching, and hybrid compression. Set limits below your API tier (~85% recommended).</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {(['gemini', 'openai', 'mistral'] as const).map(vendor => (
                        <div key={vendor} className="border rounded-lg p-3 space-y-2">
                            <p className="text-sm font-semibold capitalize">{vendor} Rate Limits</p>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="text-xs text-muted-foreground">Max RPM (requests/min)</label>
                                    <input
                                        type="number"
                                        value={settings.gateway[`${vendor}_max_rpm` as keyof typeof settings.gateway] as number}
                                        onChange={e => setSettings(prev => ({ ...prev, gateway: { ...prev.gateway, [`${vendor}_max_rpm`]: parseInt(e.target.value) || 0 } }))}
                                        className="w-full border rounded px-2 py-1 text-sm"
                                        min={1}
                                    />
                                </div>
                                <div>
                                    <label className="text-xs text-muted-foreground">Max TPM (tokens/min)</label>
                                    <input
                                        type="number"
                                        value={settings.gateway[`${vendor}_max_tpm` as keyof typeof settings.gateway] as number}
                                        onChange={e => setSettings(prev => ({ ...prev, gateway: { ...prev.gateway, [`${vendor}_max_tpm`]: parseInt(e.target.value) || 0 } }))}
                                        className="w-full border rounded px-2 py-1 text-sm"
                                        min={1000}
                                        step={1000}
                                    />
                                </div>
                            </div>
                        </div>
                    ))}
                    <div className="grid grid-cols-2 gap-4">
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div>
                                <p className="font-medium text-sm">Response Caching</p>
                                <p className="text-xs text-muted-foreground">Cache deterministic (temp=0) responses</p>
                            </div>
                            <Switch
                                checked={settings.gateway.cache_enabled}
                                onCheckedChange={checked => setSettings(prev => ({ ...prev, gateway: { ...prev.gateway, cache_enabled: checked } }))}
                            />
                        </div>
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div>
                                <p className="font-medium text-sm">Hybrid Compression</p>
                                <p className="text-xs text-muted-foreground">Local summarize → Cloud reason</p>
                            </div>
                            <Switch
                                checked={settings.gateway.hybrid_compression}
                                onCheckedChange={checked => setSettings(prev => ({ ...prev, gateway: { ...prev.gateway, hybrid_compression: checked } }))}
                            />
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ===== Save Button ===== */}
            <div className="flex justify-end pb-8">
                <Button onClick={saveSettings} disabled={saving} size="lg" className="min-w-[200px]">
                    {saving ? (
                        <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</>
                    ) : saved ? (
                        <><CheckCircle className="mr-2 h-4 w-4" /> Saved!</>
                    ) : (
                        <><Save className="mr-2 h-4 w-4" /> Save Settings</>
                    )}
                </Button>
                {saveError && <p role="alert" className="mr-4 self-center text-sm text-red-600">{saveError}</p>}
            </div>
        </div>
    );
}
