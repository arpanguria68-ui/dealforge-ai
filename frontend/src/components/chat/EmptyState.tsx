interface StarterPromptsProps {
    hasDeal: boolean;
    onPick: (prompt: string) => void;
}

const NEW_DEAL_PROMPTS = [
    'Analyze Microsoft as an acquisition target',
    'Run due diligence on a SaaS company with $50M ARR',
    'What are the main risks in acquiring a regional bank?',
];

const EXISTING_DEAL_PROMPTS = [
    'Summarize the key risks for this deal',
    'Build an IC memo from the saved analysis',
    'What valuation range does the analysis support?',
];

/** Starter prompts shown under the welcome message until the user sends something. */
export function StarterPrompts({ hasDeal, onPick }: StarterPromptsProps) {
    const prompts = hasDeal ? EXISTING_DEAL_PROMPTS : NEW_DEAL_PROMPTS;
    return (
        <div className="ml-12 flex flex-wrap gap-2" aria-label="Suggested prompts">
            {prompts.map(prompt => (
                <button
                    key={prompt}
                    type="button"
                    onClick={() => onPick(prompt)}
                    className="rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-1.5 text-xs text-white/65 transition-colors hover:border-cyan-500/30 hover:bg-white/[0.07] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
                >
                    {prompt}
                </button>
            ))}
        </div>
    );
}
