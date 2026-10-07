# DealForge AI — Original Test Case Studies

This file contains a variety of complex investment scenarios designed to test the multi-agent reasoning capabilities of DealForge AI.

---

## Case Study 1: [Project Aether] — Enterprise SaaS Consolidation
**Category:** Growth Equity / Buy-and-Build
**Target:** Skybound CRM (Mid-market CRM specifically for Logistics)

### Backstory
Skybound is a "sticky" CRM with 98% gross retention but has stalled at $15M ARR due to poor sales execution. They are currently burning $200k/month. 

### Core Data Points
- **ARR:** $15.0M (Up 5% YoY)
- **Gross Margin:** 82%
- **CAC Payback:** 28 Months (High)
- **LTV/CAC:** 2.1x
- **Burn Rate:** -$2.4M Annualized
- **Tech Debt:** Moderate; built on aging PostgreSQL version but core logic is solid.

### Agent Testing Objectives
1. **Financial Analyst:** Identify the primary drivers of the high CAC payback.
2. **Valuation Agent:** Propose a revenue multiple based on comparable vertical SaaS acquisitions.
3. **Scoring Agent:** Evaluate the "efficiency risk" vs. the "moat" of the high retention.

---

## Case Study 2: [Project Lumina] — Healthcare Interoperability
**Category:** Early-Stage Venture / Strategic Integration
**Target:** HealthNodes (AI-driven EHR mapping layer)

### Backstory
HealthNodes uses a proprietary LLM to map legacy HL7 data to modern FHIR standards. They have signed LOIs with three major health systems but face heavy HIPAA compliance scrutiny from potential acquirers.

### Core Data Points
- **Status:** Pre-revenue (Beta testing)
- **Regulatory:** HIPAA compliant (self-attested), SOC2 Type II in progress.
- **Competitors:** Redox, Particle Health.
- **Founder:** Former Chief Data Officer at Epic Systems.

### Agent Testing Objectives
1. **Compliance Agent:** Analyze the "self-attested" HIPAA status vs. the lack of SOC2.
2. **Legal Advisor:** Draft a "Conditions Precedent" list for a Seed-series investment.
3. **Market Researcher:** Compare HealthNodes' proprietary mapping engine vs. open-source alternatives.

---

## Case Study 3: [Project Solaris] — Renewable Infrastructure
**Category:** Private Equity / Infrastructure
**Target:** Horizon Solar Farm (120MW operational asset)

### Backstory
A fully operational solar farm in Spain with 15 years remaining on a Power Purchase Agreement (PPA). The current owner (a family office) wants an exit to liquidate for a different investment.

### Core Data Points
- **Asset Life:** 25 Years (10 years used)
- **Annual EBITDA:** €8.5M
- **Debt:** €45M at 4.2% Fixed
- **PPA Rate:** €0.055 / kWh (Fixed with CPI adjustment)

### Agent Testing Objectives
1. **DCF / LBO Architect:** Build a 15-year cash flow model factoring in escalating maintenance costs.
2. **Risk Assessor:** Evaluate "regulatory risk" regarding Spanish solar subsidy history.
3. **ESG Agent:** Calculate the carbon offset impact to justify a Green Bond financing.

---

## Case Study 4: [Project Nebula] — Distressed Consumer E-Commerce
**Category:** Turnaround / Special Situations
**Target:** ModHome (Luxury D2C Furniture)

### Backstory
ModHome was valued at $200M in 2021. Post-COVID, they are overstocked with $40M in inventory and have an unpaid vendor debt of $12M. Shipping costs have eaten their margin.

### Core Data Points
- **Revenue:** $80M (Down 30% YoY)
- **Inventory:** $40M (Book Value)
- **Net Debt:** $25M (Covenant breach imminent)
- **Warehouse Leases:** High-cost hubs in NYC and LA.

### Agent Testing Objectives
1. **Business Analyst:** Propose a "Rightsizing" plan (which warehouses to close?).
2. **Scoring Agent:** Assign a "Probability of Bankruptcy" score based on current liquidity.
3. **Integration Planner:** Outline steps for a "Pre-Packaged Chapter 11" if the turnaround fails.
