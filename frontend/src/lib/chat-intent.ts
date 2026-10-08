const DEAL_ANALYSIS_TERMS = /\b(?:due diligence|valuation|dcf|lbo|investment memo|financial statements?|cash flow|ebitda|revenue forecast|market research|competitor analysis|risk assessment|company analysis|business analysis|target company|case study|10-k|10-q|sec filing)\b/i;
const DEAL_ACTION = /\b(?:analy[sz]e|assess|evaluate|screen|research|review|value|build|prepare|run|perform|conduct|model)\b[\s\S]{0,120}\b(?:deal|acquisition|acquire|buyout|merger|m\s*&\s*a|company|business|target|valuation|dcf|lbo|investment memo|financial|market|risk|report|forecast)\b/i;

export function isDealAnalysisRequest(
    prompt: string,
    hasFiles = false,
    hasExistingDeal = false,
): boolean {
    if (hasFiles || hasExistingDeal) return true;
    const positiveRequest = prompt.replace(/\b(?:do not|don't|dont|never)\b[^.!?\n]*/gi, ' ');
    return DEAL_ANALYSIS_TERMS.test(positiveRequest) || DEAL_ACTION.test(positiveRequest);
}
