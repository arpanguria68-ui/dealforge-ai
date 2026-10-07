from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

class FinancialMetrics(BaseModel):
    revenue_historical: Dict[str, float] = Field(default_factory=dict, description="Historical revenue by year/period")
    ebitda_historical: Dict[str, float] = Field(default_factory=dict, description="Historical EBITDA by year/period")
    net_income_historical: Dict[str, float] = Field(default_factory=dict, description="Historical net income by year/period")
    
    revenue_projected: Dict[str, float] = Field(default_factory=dict, description="Projected revenue (Year 1-5)")
    ebitda_projected: Dict[str, float] = Field(default_factory=dict, description="Projected EBITDA (Year 1-5)")
    
    margins: Dict[str, float] = Field(default_factory=dict, description="Standard margins (Gross, EBITDA, Net)")
    growth_rates: Dict[str, float] = Field(default_factory=dict, description="CAGR and annual growth rates")

class DealTerms(BaseModel):
    asking_price: Optional[float] = None
    enterprise_value: Optional[float] = None
    equity_value: Optional[float] = None
    transaction_structure: Optional[str] = None
    sources_and_uses: Dict[str, Any] = Field(default_factory=dict)

class CompanyProfile(BaseModel):
    name: str
    ticker: Optional[str] = None
    industry: str
    sector: str
    employee_count: Optional[int] = None
    headquarters: Optional[str] = None
    main_products: List[str] = Field(default_factory=list)

class FactSource(BaseModel):
    agent_name: str
    tool_name: str
    chunk_id: Optional[str] = None
    confidence: float
    timestamp: str

class DealFactBase(BaseModel):
    """
    The Single Source of Truth (SSoT) for a deal's quantitative and 
    structural data. Populated by IngestionAgent and consumed by 
    all valuation/financial agents.
    """
    profile: CompanyProfile
    metrics: FinancialMetrics
    terms: DealTerms
    
    # Mapping of fact_path (e.g., "metrics.revenue_historical.2023") to its source
    source_map: Dict[str, FactSource] = Field(default_factory=dict)
    
    missing_critical_data: List[str] = Field(default_factory=list)
    version: int = 1
    last_updated: str = Field(default_factory=lambda: "2026-03-16")
