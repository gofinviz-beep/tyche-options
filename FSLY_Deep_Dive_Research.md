# Fastly, Inc. (FSLY) — Deep-Dive Equity Research

**Date:** March 26, 2026
**Analyst:** Senior Equity Research
**Sector:** Internet / Edge Cloud Infrastructure
**Investment Horizon:** Short-term (3 months) and Long-term (3–5 years)

---

## Module 0: The thesis in one line

> The CDN industry is undergoing the same structural shift as early-2000s managed security services — commodity delivery collapsing into bundled security-and-delivery platforms, with the winners determined by who can cross-sell fastest — and Fastly's inflection is real (first profitable year, 55% RPO growth, security at 32% growth), but at $24.58 the stock already prices in two years of flawless execution, making it a "right company, wrong price" situation until a macro pullback offers a margin of safety.

---

## Module 1: Industry cycle — where are we in the cycle?

### 1.1 The boom-bust history of the CDN/edge cloud industry

The content delivery network industry has moved through four distinct phases since its founding. Each phase redefined who won and who got destroyed.

| Phase | Period | Trigger | Price / margin move | Duration | Survivors vs. casualties |
|-------|--------|---------|---------------------|----------|--------------------------|
| **Genesis & dot-com boom** | 1998–2001 | Tim Berners-Lee's 1995 MIT challenge. Akamai founded 1998, IPO 1999. Digital Island, Speedera, iBeam emerge. | Akamai IPO at $26, peaked above $300 (Oct 1999). Revenue growth >100% YoY. | ~3 years | **Survived:** Akamai. **Destroyed:** Digital Island (acquired), iBeam (bankrupt 2002), most first-gen CDNs. |
| **Consolidation & video rise** | 2002–2012 | Flash video (YouTube 2005), adaptive bitrate streaming (HLS), HD content. Akamai acquires Speedera (2005). Limelight Networks IPO (2007). | Akamai revenue grew from $164M (2003) to $1.37B (2012). Pricing fell 15–20% annually but volume more than offset. | ~10 years | **Survived:** Akamai (dominant), Limelight (struggling). **Destroyed:** Most smaller CDNs consolidated out. |
| **Cloud disruption & commoditization** | 2013–2019 | AWS CloudFront (2008, scaled by 2013), Cloudflare (founded 2009, IPO Sept 2019), Fastly (founded 2011, IPO May 2019). Free/cheap CDN tiers commoditize basic delivery. | Akamai growth slowed to 5–8%. Cloudflare grew >40% annually. CDN-only pricing declined 20%+ per year. Pure delivery margins compressed. | ~7 years | **Survived:** Akamai (pivoted to security), Cloudflare (grew aggressively), Fastly (IPO'd). **Weakened:** Limelight (merged with Edgecast/Edgio, later filed Chapter 11 in 2024). |
| **Pandemic boom & AI-driven expansion** | 2020–present | COVID-19 drove 40%+ traffic surge. Post-pandemic: AI/agentic traffic, API explosion, edge compute demand. | Fastly peaked at $128.83 (Oct 2020), crashed to $5.00 (Apr 2025), recovered to ~$25. Cloudflare grew from $287M (2019) to $2.17B (2025) revenue. | Ongoing | **Winning:** Cloudflare (30% growth), Fastly (turnaround). **Declining:** Akamai CDN delivery (flat), Edgio (bankrupt). |

**So what?** The CDN industry has a 25-year pattern: each cycle kills the pure-play delivery providers and rewards those who bundle security, compute, and performance on a single platform. We are in the middle of the fourth cycle. The question is not whether bundling wins — it is whether Fastly can bundle fast enough to stay relevant against Cloudflare's velocity.

### 1.2 Where are we now?

The industry is in a **growth phase with structural divergence** — not a supercycle.

Traditional CDN delivery is growing at 6–7% annually. Fastly's CFO Rich Wong stated on the Q4 2025 call that "the market that we see is about 6% or 7% year-over-year growth" for network services, and "12% to 13% year-over-year growth" for security. The total CDN market is approximately $30 billion (2025), projected to reach $36 billion in 2026 and $40 billion by 2032.

But the real action is in the high-growth sub-segments:

- **Edge functions on CDN:** $5.9B (2025) growing at 17.8% CAGR to $13.5B by 2030.
- **Edge AI market:** $25B (2025) projected to reach $118B by 2033.
- **Overall edge computing:** $265B (2025), growing at 15% annually to $450B by 2029.

What has changed in the last 6 months:

1. **AI traffic became measurable.** Fastly published telemetry data showing agentic AI requests growing quarter-over-quarter. CEO Kip Compton: "We're seeing an increase in traffic related to agents... they often check a lot more websites than you might."
2. **Price erosion contracted sharply.** Fastly's price erosion fell from mid-teens historically to mid-single digits in Q4 2025. This is a structural shift — customers are paying for performance and resilience, not just bandwidth.
3. **Resiliency became a buying criterion.** Compton noted "recent events in the industry that has called more attention to the value of resiliency in an edge platform" — an oblique reference to competitor outages that drove traffic to Fastly.

**So what?** This is not a supercycle — it is a structural divergence. Companies selling commodity bandwidth are dying (Edgio: bankrupt). Companies selling security + performance + compute on the edge are thriving. Fastly is transitioning from the first camp to the second. The timing of that transition is the entire investment thesis.

### 1.3 The demand explosion — what new forces are driving demand?

Four demand drivers are accelerating simultaneously:

| Demand driver | Quantification | % of demand contribution (est.) |
|---------------|---------------|--------------------------------|
| **Agentic AI traffic** | AI bots generate 3–10x more HTTP requests per "user session" than human browsers. Global mobile data traffic expected to reach 403 exabytes/month by mid-2025 — exceeding all 2017 internet traffic. | ~15% of incremental traffic growth |
| **API security expansion** | API attacks grew 600%+ from 2021–2025. Every agentic AI system creates new API endpoints requiring discovery, inventory, and protection. Fastly launched API Discovery (Q3 2025) and API Inventory (Q4 2025). | ~25% of security revenue growth |
| **Edge inference workloads** | IDC forecasts 50% of all enterprise AI inference will be processed at edge/endpoints by 2030, up from <10% today. Fastly customers are running inference on Compute@Edge. | ~5% of revenue (early, growing fast) |
| **Resilience-driven traffic migration** | Customers directing traffic to Fastly after competitor outages. A Fortune 500 restaurant chain switched to Fastly and "experienced their best digital day on record." | ~10% of Q4 network services outperformance |

**So what?** AI is not yet the primary revenue driver — security cross-sell is. But AI traffic is the tailwind that inflates the base of every other metric: more traffic means more delivery revenue, more API calls to protect, more bot management to sell. It is the rising tide, not the ship.

### 1.4 The supply problem — where is the structural bottleneck?

The bottleneck in edge cloud infrastructure is not servers. It is **memory components and edge-optimized real estate**.

- **Memory pricing:** CFO Rich Wong disclosed that memory component costs are rising 25–75% year-over-year. This is a direct result of AI training demand consuming DRAM and NAND supply globally. CEO Compton clarified: "The 25% to 75% is on the memory component itself. Not the overall unit cost."
- **Edge PoP density:** Fastly operates 80+ PoPs. Cloudflare has 330+. Akamai has 4,200+. Expanding edge presence requires co-location agreements, power contracts, and local peering — none of which scale overnight.
- **Specialized talent:** Edge computing engineers who understand both networking and AI/ML are scarce. This is a hiring bottleneck across the industry.

The expansion lead time for meaningful new capacity is 6–12 months. Fastly is front-loading 2026 CapEx (10–12% of revenue, up from 5% in 2025) specifically to "ensure we have adequate equipment, given recent supply chain constraints" (Wong).

**So what?** The supply bottleneck favors incumbents with existing edge infrastructure. It does not, however, create a supercycle — Cloudflare's 330+ PoPs can absorb enormous traffic growth without pricing power accruing to smaller players. Fastly benefits from rising demand but does not control pricing power in the way a true supply-constrained producer would.

### 1.5 The industry analogy

**The analogy: Early-2000s managed security services (MSS) market.**

In the early 2000s, enterprise security shifted from point products (firewalls, IDS) to managed security services — bundled platforms where a single vendor monitored, detected, and responded to threats. The market was fragmented. Pure-play firewall vendors (NetScreen, SonicWall) were acquired or marginalized. The winners were those who bundled security into broader infrastructure plays: Cisco (acquired IronPort, Sourcefire), IBM (acquired ISS), and eventually Palo Alto Networks and CrowdStrike as cloud-native successors.

**Where the analogy holds:**

| Structural similarity | MSS (2002–2010) | CDN/Edge (2020–present) |
|----------------------|-----------------|------------------------|
| Commodity product becoming platform | Firewall → managed security platform | CDN delivery → edge cloud platform |
| Cross-sell as growth engine | Firewall customers upsold to IDS, SIEM, SOC | CDN customers upsold to WAF, bot mgmt, API security |
| Consolidation of point solutions | Dozens of vendors → 5 dominant platforms | Dozens of CDNs → 3–4 dominant edge platforms |
| New demand driver forcing rebundling | Compliance (SOX, PCI) forced managed approach | AI/API explosion forces security-at-the-edge |
| Acquisitions as portfolio accelerator | Cisco bought 10+ security companies 2003–2010 | Fastly acquired Signal Sciences; Akamai acquired Linode |

**Where the analogy breaks:**

1. **Cloudflare is already cloud-native.** In the MSS analogy, the disruptors (Palo Alto, CrowdStrike) emerged later. In CDN, Cloudflare is already the cloud-native bundler — and growing at 30%. Fastly is not the disruptor; it is the legacy player trying to catch the disruptor.
2. **Free tier dynamics.** Cloudflare's free tier creates a funnel with no MSS equivalent. This makes customer acquisition costs structurally lower for Cloudflare.
3. **Scale economics differ.** MSS companies could differentiate on analyst expertise. CDN performance differentiation is measurable and narrow — Fastly's millisecond cache purge advantage is real but not a category-defining moat.

**So what?** The MSS analogy says the market structure is right: bundling wins, cross-sell drives margins, and 3–4 platforms will dominate. The question is whether Fastly is Cisco (a strong incumbent that successfully pivoted) or SonicWall (an incumbent that got acquired because it could not keep pace). The next 12 months of security revenue growth will answer this question.

---

## Module 2: The company in the context of the industry

### 2.1 The value chain map — where is the moat?

```
Origin Servers → Edge PoPs → Last-Mile Delivery
                    ↓
              Security Layer (WAF, DDoS, Bot Mgmt, API Security)
                    ↓
              Compute Layer (Compute@Edge, serverless functions)
                    ↓
              Observability (logging, real-time analytics, dashboards)
```

| Value chain step | Global players | Barriers to entry | Fastly's position |
|------------------|---------------|-------------------|-------------------|
| **Origin hosting** | Thousands (AWS, GCP, Azure, on-prem) | Low — commodity cloud | Not present |
| **Edge PoPs (delivery)** | 5–6 at scale (Akamai, Cloudflare, Fastly, AWS CloudFront, Google CDN, Azure CDN) | High — co-location contracts, peering agreements, capital. 6–12 month build. | 80+ PoPs. Smallest of the big 3 but highest performance per-PoP. |
| **Security at the edge** | 4–5 leaders (Cloudflare, Akamai, Fastly, Imperva/Thales, F5) | High — requires years of attack data, false-positive tuning, 7-year Gartner recognition. | Strong. Signal Sciences acquisition (2020). Only company with 7 consecutive years of Gartner Customers' Choice for WAAP. |
| **Edge compute** | 3–4 (Cloudflare Workers, Fastly Compute@Edge, AWS Lambda@Edge, Deno Deploy) | Medium — requires programmable edge runtime and developer ecosystem. | Competitive. Compute@Edge based on WebAssembly. Growing 78% YoY but from small base ($6.4M/quarter). |
| **Observability** | Many (Datadog, Splunk, New Relic + CDN-native tools) | Low for standalone. High for integrated real-time edge observability. | Launched custom dashboards and alerts in Q4 2025. Early. |

**The moat** sits at the intersection of security and edge delivery — specifically, the ability to inspect, filter, and accelerate traffic in a single pass at the edge without routing to separate security appliances. Fastly's advantage is sub-millisecond cache purging (competitors take seconds) and a programmable edge that runs custom logic per-request. The moat is narrow but measurable.

**So what?** Fastly's moat is performance + integrated security, but it is only 80 PoPs wide. Cloudflare's 330+ PoP network provides equivalent functionality with broader coverage. Fastly must win on depth (more sophisticated security, better performance per-PoP) because it cannot win on breadth.

### 2.2 Brief company history — only the milestones that matter

| Year | Event | Strategic significance |
|------|-------|----------------------|
| 2011 | Founded by Artur Bergman (ex-CTO of Wikia) in San Francisco | Built on the thesis that CDNs should be programmable and real-time, not batch-configured. |
| 2019 | IPO on NYSE at ~$16/share; raised $192M | Validated the edge cloud category. Entered public markets during CDN commoditization wave. |
| 2020 (Aug) | Acquired Signal Sciences for $775M ($200M cash + $575M stock) | The pivotal strategic decision. Gave Fastly a best-in-class WAF and WAAP capability. This acquisition is the foundation of the entire security revenue line. |
| 2020 (Oct) | Stock hits all-time high of $128.83 | Pandemic + TikTok deal speculation drove a retail frenzy. Market cap briefly exceeded $17B on ~$290M annual revenue — a 58x EV/Sales. |
| 2021–2022 | Stock collapses 94% from peak to ~$7.44 | TikTok uncertainty, revenue deceleration, CEO transition (Bergman → Bixby), and the broader SaaS multiple compression destroyed shareholder value. |
| 2024 (Jan) | Kip Compton joins as Chief Product Officer | Ex-Cisco SVP (Strategy & Business Development). MIT CS/EE, Wharton MBA. Brought enterprise go-to-market discipline. |
| 2024 (Dec) | Issues $150M of 7.75% convertible notes due 2028; retires $157.9M of 2026 notes | Refinanced near-term debt at a high coupon, extending maturity runway. |
| 2025 (Apr) | Stock hits all-time low of $5.00 | Maximum pessimism. Revenue growing but losses persisting. No clear path to profitability visible to the market. |
| 2025 (Jun) | Kip Compton appointed CEO, replacing Todd Nightingale | Third CEO in three years. Nightingale lasted ~18 months. Compton promoted from CPO — rare internal elevation signaling board's bet on product-led strategy. |
| 2025 (Dec) | Issues $180M of 0% convertible notes due 2030; executes capped call | Dramatically improved terms vs. 2028 notes (0% vs. 7.75%). Market signaling confidence in the turnaround. |
| 2025 (FY) | First profitable fiscal year (non-GAAP). Revenue $624M (+15%). FCF $45.8M. | The inflection point. After years of losses, the business model works. |

**So what?** Fastly has compressed a decade of strategic pivots into five years: IPO, transformative acquisition, stock collapse, three CEO changes, and now profitability. The Signal Sciences acquisition was the right move — security is now 21% of revenue and growing 32%. The question is whether the organizational instability (three CEOs since 2020) has left enough institutional muscle to execute against Cloudflare's relentless expansion.

### 2.3 Products, revenue breakdown, and market share

| Product line | Q4 2025 revenue | % of total | Margin profile | Growth trend |
|-------------|----------------|-----------|---------------|-------------|
| **Network services** (CDN, load balancing, image optimization, video delivery) | $130.8M | 76% | Higher margin at scale — volume drives gross margin leverage. CFO: "the number one thing that drives our margins up is volume." | Accelerating: 19% YoY in Q4 (up from ~12% a year ago). Outpacing 6–7% market growth. |
| **Security** (WAF, DDoS protection, bot management, API security) | $35.4M | 21% | Subscription-based, recurring. Higher gross margin than consumption-based delivery. | Accelerating: 32% YoY in Q4 (up from 30% in Q3). The thesis driver. |
| **Other** (Compute@Edge, observability) | $6.4M | 3.7% | Early-stage. Low margin today as compute workloads are being subsidized to drive adoption. | Accelerating: 78% YoY in Q4. Tiny base but fastest-growing segment. |

**Full year 2025:** $624.0M total revenue (+14.7% YoY), up from $543.7M in 2024.

**Market share context:** In CDN delivery by website adoption, Cloudflare dominates at 82.3%, Fastly holds 3.4%, Akamai 2.9% (W3Techs). By enterprise revenue, Akamai leads (~34%), Cloudflare (~28%), AWS CloudFront (~22%). Fastly is a distant fourth. But in premium, performance-sensitive delivery (streaming, e-commerce, real-time apps), Fastly competes directly with the top two.

**The earnings driver of the thesis is security.** Not because it is the largest segment (it is not), but because it is where the margin inflection happens. Security revenue is subscription-based, higher-margin, and stickier than consumption-based delivery. Every percentage point of revenue mix shift toward security improves gross margin by an estimated 50–100 basis points.

**So what?** Fastly is a $624M revenue company in a $30B+ market — small enough to grow quickly, but small enough to be competitively vulnerable. The security segment at 21% of revenue and growing 32% is the single most important number in the entire report. If security reaches 30% of revenue by FY2027, the business model transforms permanently. If it stalls at 20%, Fastly remains a commodity CDN with better performance benchmarks.

---

## Module 3: The catalyst — what changes now?

### 3.1 The primary catalyst

**Security-driven cross-sell and RPO growth are transforming Fastly from a usage-based CDN into a committed-revenue platform company.**

The numbers:

- **RPO (Remaining Performance Obligations):** $353.8M at Q4 2025, up 55% YoY. The current portion (70%, or ~$248M) grew 37% YoY. This is the largest committed revenue base in company history.
- **Security revenue growth:** 32% YoY in Q4, accelerating from 30% in Q3 and representing the fourth consecutive quarter of acceleration.
- **Net retention rate:** 110%, up from 106% in Q3 and 102% a year ago. This means existing customers are spending 10% more year-over-year — the highest expansion rate in at least two years.

**Why this matters NOW:** The go-to-market transformation that Kip Compton and CFO Rich Wong have executed is producing measurable results. Wong explained: "We went through a go-to-market transformation over the past 12 to 18 months. Part of that transformation has been around getting to know our customers better, really aligning our sales teams to our customer accounts."

The specific mechanism is cross-sell. CEO Compton: "We have seen material cross-sell activity in our large accounts, and that cross-sell activity brings in portfolios like security and compute. And that starts to transform the relationship in many ways to one that's more strategic."

**Why the market has not fully priced this in:** The RPO inflection is only two quarters old. Analysts are still modeling Fastly as a consumption-based CDN with inherent volatility. The shift to committed revenue is not yet reflected in consensus models, which still apply a "CDN discount" to the multiple. If RPO continues growing at 40%+ for two more quarters, the narrative shifts from "volatile CDN" to "predictable platform" — and the multiple re-rates.

**Quantified impact:** If current RPO of $353.8M converts at historical rates (70% within 12 months), that provides ~$248M of committed revenue into FY2026 — covering 35% of the $710M revenue midpoint before a single new deal is signed.

**So what?** The RPO growth is the single most under-appreciated data point in Fastly's financials. It means the revenue base is becoming predictable at the exact moment the company is turning profitable. This is the classic SaaS inflection — but it is happening inside an infrastructure company, which makes it harder for generalist investors to recognize.

### 3.2 Secondary catalysts

**Catalyst A: AI bot management monetization**

Fastly was the first CDN to support RSL (Really Simple Licensing), a protocol enabling media companies to enforce content rights agreements with AI crawlers. CEO Compton: "The discussion has shifted from perhaps last summer, how do you block it, to a much more nuanced and sophisticated conversation now about, how do you optimize for it."

- **Timeline:** Revenue contribution growing through 2026 as media customers adopt AI bot management.
- **Impact:** Incremental to security revenue. Estimated $5–10M contribution in FY2026, accelerating as agentic AI traffic increases.

**Catalyst B: API security suite completion**

Fastly launched API Discovery (Q3 2025) and API Inventory (Q4 2025). CEO Compton stated the company is "about halfway through the journey" on API security. Some of the largest new deals are on API use cases.

- **Timeline:** Additional API security features expected through FY2026–2027.
- **Impact:** Expands the addressable TAM within existing security customers. API security is a ~$5B market growing 25%+ annually.

**Catalyst C: Price erosion contraction**

CFO Wong disclosed that price erosion fell from mid-teens historically to mid-single digits in Q4 2025. This means Fastly is retaining more revenue per unit of traffic — a direct margin tailwind.

- **Timeline:** Already occurring. Expected to sustain through 2026 as go-to-market focuses on "performance-matters" customers.
- **Impact:** Every 5 percentage points of reduced price erosion on $525M of network services revenue translates to ~$26M of preserved revenue annually.

**So what?** The secondary catalysts reinforce rather than replace the primary thesis. AI bot management and API security expand the security TAM. Price erosion contraction protects the CDN base. None of these alone would justify investment — but together they create a compounding effect on the margin inflection.

### 3.3 The margin inflection model

| Metric | FY2023 (trough) | FY2025 (inflection) | FY2026 (guide) | FY2027E (projection) |
|--------|-----------------|--------------------|-----------------|-----------------------|
| Revenue | $506M | $624M | $710M (midpoint) | $825–850M (est. 16–20% growth) |
| Gross margin (non-GAAP) | ~56% | 60.9% | 63% (±50bps) | 64–65% (scale leverage) |
| Operating margin (non-GAAP) | -23% | 4.0% | 8.0% (midpoint) | 10–12% (est.) |
| Non-GAAP operating income | -$116M | $25M | $55M (midpoint) | $83–102M (est.) |
| Free cash flow | -$41M | $45.8M | $45M (midpoint) | $60–80M (est.) |

**What needs to happen for each step:**

1. **Gross margin 60.9% → 63%:** Revenue growth above CapEx absorption rate. New APJ PoPs come online mid-2026, temporarily diluting margin in Q2–Q3 before traffic fills capacity. Q1 and Q4 will be higher (CFO confirmed this seasonality).
2. **Operating margin 4% → 8%:** OpEx discipline. Q4 2025 OpEx was $89.2M — management held the line. If OpEx grows at 6–8% while revenue grows 14%, the math works mechanically.
3. **Operating margin 8% → 10–12% (FY2027E):** Requires security reaching 25–28% of revenue mix, driving subscription revenue above 30% of total. This is the non-trivial step — it depends on continued cross-sell velocity.

**The incremental gross margin tells the story.** In Q4, trailing incremental gross margin was 76%, up from 58% in Q3. This means 76 cents of every incremental dollar of revenue fell to gross profit. At this rate, scale alone drives the margin expansion — without any mix shift.

**So what?** The margin inflection is real, mechanical, and partially self-reinforcing (more revenue → better margins → more investment capacity → more security products → more cross-sell → more revenue). The risk is that this flywheel requires 14%+ revenue growth to sustain. If growth drops below 10%, the fixed-cost base becomes a headwind rather than a tailwind.

---

## Module 4: Head-to-head — who benefits more and why?

### 4.1 The side-by-side comparison table

| Parameter | Fastly (FSLY) | Cloudflare (NET) | Akamai (AKAM) |
|-----------|--------------|-----------------|---------------|
| **FY2025 revenue** | $624M | $2,168M | $4,208M |
| **Revenue growth (FY2025 YoY)** | 15% | 30% | 5% |
| **Q4 2025 growth (YoY)** | 23% | 34% | 7% |
| **2026 revenue guidance** | $700–720M (+14%) | $2,785–2,795M (+29%) | Not provided (~5–7% est.) |
| **Gross margin (non-GAAP, Q4 2025)** | 64.0% | ~78% (est.) | ~63% (est.) |
| **Operating margin (non-GAAP, FY2025)** | 4.0% | 14.0% | ~27% (est.) |
| **Free cash flow (FY2025)** | $45.8M | ~$350M (est.) | ~$900M (est.) |
| **Market cap** | ~$3.8B | ~$47B | ~$15B |
| **EV/Sales (trailing)** | 5.8x | 21.7x | 3.6x |
| **EV/Sales (forward, FY2026)** | 5.1x | 16.8x | ~3.4x |
| **Edge PoPs / network size** | 80+ | 330+ | 4,200+ |
| **Security portfolio maturity** | Medium — WAF (7yr Gartner Choice), DDoS, bot mgmt, API security (building). Acquired via Signal Sciences (2020). | High — WAF, DDoS, Zero Trust (SASE), email security, CASB. Organically built over 10+ years. | High — WAF, DDoS (Prolexic), bot mgmt, API security, microsegmentation (Guardicore). |
| **AI positioning** | AI bot mgmt, RSL protocol support, Compute@Edge inference. "About halfway through the journey." | AI inference at edge (Workers AI), AI Gateway, Vectorize. Most advanced edge AI stack. | Cloud computing pivot (Linode acquisition). Less edge AI, more cloud AI. |
| **Balance sheet (net cash/debt)** | ~Neutral ($362M cash vs. ~$520M convertible debt) | Net cash ~$1.7B | Net cash ~$2B+ |
| **Customer concentration risk** | High — top 10 = 34% of revenue | Low — diversified across millions of customers | Medium — enterprise-focused but diversified |
| **Net retention rate** | 110% (improving) | ~118% (est.) | ~105% (est.) |
| **Key strategic advantage** | Sub-millisecond cache purging. Programmable edge. Best real-time performance. | Developer ecosystem (Workers), free tier funnel, broadest product suite, largest edge network outside Akamai. | Scale (4,200+ nodes), enterprise relationships, massive cash generation. |

### 4.2 The verdict: who benefits more?

Cloudflare is the better-positioned company for the current industry cycle. It is growing 2x faster than Fastly, has 4x the edge network, a vastly broader product portfolio, a stronger balance sheet, and trades at a premium multiple that the market has consistently rewarded with continued outperformance. If you are building a core position in edge cloud infrastructure, Cloudflare is the default choice.

But that is not the right framing for Fastly. Fastly is not a momentum play — it is a turnaround play. The comparison that matters is not "who is the better company today" (Cloudflare, obviously) but "which stock offers more asymmetric upside from current prices." Cloudflare at $47B market cap and 21.7x EV/Sales must continue growing at 30%+ just to justify its multiple. Fastly at $3.8B and 5.8x EV/Sales needs only to prove that its margin inflection is sustainable to re-rate significantly.

Akamai is the legacy incumbent generating enormous cash flow ($900M+ FCF) but growing at 5%. It is a value play, not a growth play, and its CDN delivery business is structurally declining. Akamai's pivot to cloud computing (via Linode) is strategically sensible but unproven.

The honest assessment: Cloudflare has the highest probability of compounding wealth over 3–5 years. Fastly has the highest potential return if the turnaround thesis plays out — but also the highest probability of permanent capital impairment if it does not. Akamai is the defensive play with limited upside.

**So what?** Within this triad, Fastly is the high-beta, turnaround bet. The investment case requires a differentiated view that the market is undervaluing Fastly's security cross-sell trajectory and RPO durability. If you do not have that differentiated view, Cloudflare is the safer allocation.

---

## Module 5: Operational and financial deep dive

### 5.1 Infrastructure and capacity

| Region | PoP presence | Key products served | Expansion plans (2026) |
|--------|-------------|--------------------|-----------------------|
| **North America** | ~40 PoPs (majority of network) | Network services, security, compute | Maintenance and capacity upgrades |
| **Europe** | ~25 PoPs | Network services, security | Moderate expansion |
| **Asia-Pacific (APJ)** | ~15 PoPs | Network services | **Primary expansion focus.** CFO: "We are opening up additional [PoPs] out there to support the business." |

Infrastructure CapEx for FY2026 is guided at 10–12% of revenue ($70–86M), up from 5% ($31M) in FY2025. The CFO broke this down:

- **Growth CapEx:** "The vast majority of that infrastructure CapEx is not for the maintenance side, but more for the growth support side."
- **Timing shift:** ~$10M of planned Q4 2025 CapEx slipped to 2026 (worth ~1.5% of annual revenue).
- **Component costs:** Memory inflation of 25–75% YoY is inflating per-unit CapEx. The increased spending is both more capacity AND higher per-unit costs.

Fastly's structural advantage is software-defined infrastructure. The company's edge platform is designed to maximize throughput per physical server, requiring less hardware than legacy CDN architectures. CEO Compton: "We believe we have a very efficient infrastructure."

**So what?** Fastly is doubling infrastructure investment at the exact moment costs are rising. This is the right strategic move (you must build ahead of demand), but it compresses FY2026 free cash flow and creates execution risk if traffic does not fill the new capacity. The APJ expansion is the critical bet — Asia-Pacific is where traffic growth is fastest.

### 5.2 The balance sheet — plain English quality assessment

**Cash position:** Fastly held $362M in cash, equivalents, marketable securities, and investments at Q4 2025 end. This is a sequential increase of $19M over Q3 — the business is now cash-generative.

Expressed simply: for every dollar of Fastly's $3.8B market cap, approximately 9.5 cents is backed by cash and investments. This is not a cash-rich story.

**Debt structure — the convertible note stack:**

| Tranche | Principal outstanding | Coupon | Maturity | Conversion price | Dilution risk |
|---------|----------------------|--------|----------|-----------------|---------------|
| **2026 Notes** | ~$188.6M | 0% | 2026 | ~$113/share (well above current) | Minimal dilution. Will likely be settled in cash. **Near-term refinancing risk.** |
| **2028 Notes** | $150M | 7.75% | June 2028 | $19.74/share | **High dilution risk.** Conversion price below current stock price ($24.58). Represents ~7.6M potential new shares. |
| **2030 Notes** | $180M | 0% | December 2030 | $15.26/share | **Highest dilution risk.** Deep in the money. Represents ~11.8M potential new shares. Partially offset by $18M capped call (cap at $23.04). |

**Total convertible debt:** ~$518.6M. Against $362M cash, Fastly has net debt of ~$157M. This is not alarming, but the near-term risk is real: the 2026 notes ($188.6M) mature this year. They will need to be settled — either with cash (consuming over half the cash reserve) or refinanced. The 7.75% coupon on the 2028 notes costs ~$11.6M annually — expensive debt for a company only recently profitable.

**Free cash flow transformation:**

| Year | Operating cash flow | CapEx | Free cash flow |
|------|-------------------|-------|----------------|
| FY2024 | $16.4M | ($52.1M) | -$35.7M |
| FY2025 | $94.4M | ($48.6M) | +$45.8M |
| FY2026E (guide) | ~$115M (est.) | ($70–86M) | +$40–50M (guided) |

The $81.6M improvement in FCF from FY2024 to FY2025 is the most important financial data point in the balance sheet story. It proves the business can generate cash when revenue grows above the fixed-cost base. The FY2026 FCF guide of $40–50M is actually lower than FY2025's $45.8M because of the infrastructure CapEx ramp — but the underlying operating cash flow is growing.

**CFO-to-PAT reconciliation:** The company's non-GAAP net income was $19.7M for FY2025. Operating cash flow was $94.4M — 4.8x net income. The gap is largely stock-based compensation ($120M+ annually) and depreciation. This is a high-SBC company: stock comp runs at approximately 19% of revenue. This dilutes shareholders and inflates non-GAAP earnings relative to GAAP.

**So what?** The balance sheet is adequate but not comfortable. The 2026 convertible note maturity is the near-term stress point. If the stock stays above $20, Fastly can manage. If a macro event pushes it below $15, the 2028 and 2030 notes become deeply dilutive, and refinancing the 2026 notes becomes expensive. The balance sheet is a "works as long as things go right" situation — which is not the same as "strong."

### 5.3 Management execution audit — walking the talk

| Promise | Quarter made | Deadline | Outcome | Assessment |
|---------|-------------|----------|---------|------------|
| Q3 2025 revenue guidance: $155–159M | Q2 2025 call | Q3 2025 | Actual: $163.6M. Beat high end by $4.6M (+3%). | Beat |
| Q4 2025 revenue guidance: $159–163M | Q3 2025 call | Q4 2025 | Actual: $172.6M. Beat high end by $9.6M (+6%). | Significant beat |
| FY2025 original revenue guidance: $575–585M | Q4 2024 call (Feb 2025) | FY2025 | Actual: $624.0M. Beat high end by $39M (+6.7%). | Major beat |
| FY2025 original FCF guidance: -$15M (midpoint) | Q4 2024 call | FY2025 | Actual: +$45.8M. Swung from guided loss to significant positive FCF. | Dramatic beat |
| Gross margin target: flat to FY2024 (58.8%) | Q4 2024 call | FY2025 | Actual: 60.9%. Beat by 210bps. | Beat |
| "Accelerate growth and drive towards profitability" | CEO Compton, first call as CEO (Q2 2025) | Ongoing | Q4 2025 delivered 23% growth (highest in 3+ years) and first profitable fiscal year. | Delivered |

**Management credibility grade: HIGH — with the caveat that the sample size is only two quarters under the current CEO.**

Kip Compton has been CEO for less than 10 months. In that time, every quarter has beaten guidance, and beaten it substantially. The go-to-market transformation (better customer alignment, disciplined pricing, security-led sales) is producing measurable results. CFO Rich Wong, in his second quarter, demonstrated rigorous financial planning — he described building guidance from "18 different calibrations" and customer-by-customer traffic models.

The risk is institutional memory. Fastly has had three CEOs since 2020 (Bixby, Nightingale, Compton). Each promised transformation. Each partially delivered. The organization has been in constant strategic flux, and it is fair to question whether the current momentum is durable or whether another leadership change would reset progress.

**The most revealing quote from the most recent earnings call:**

> "We are now at inflection point where we believe we are a strong share gainer in our markets and demonstrating consistent profit expansion to scale."
> — Rich Wong, CFO, Q4 2025 Earnings Call

This is a CFO in his second quarter making a declarative statement about inflection. It is either prescient or premature. The next two quarters will determine which.

**So what?** Management credibility is earned, not declared. Two quarters of material beats is encouraging but not conclusive. The true test comes in Q2–Q3 2026, when new APJ PoPs come online (diluting margins temporarily) and the Q4 seasonal traffic strength fades. If management can sustain 14%+ growth and 60%+ gross margins through the seasonally weaker quarters, credibility moves from "high with a caveat" to simply "high."

---

## Module 6: Risks — where the thesis fails

### 6.1 The primary risk (thesis-killing risk)

**Valuation compression on growth deceleration.**

At $24.58, Fastly trades at 5.8x EV/Sales on trailing revenue and 5.1x on FY2026 guided revenue. The stock has risen ~400% from its April 2025 low of $5.00. This rally prices in not just the current inflection but continued acceleration.

Here is the kill scenario: Fastly guides for 14% FY2026 growth. If Q1 2026 comes in at the low end of guidance ($168M) and management signals macro caution on the Q1 call, the stock re-rates to a "decelerating growth" multiple. For a company growing 10–12% with 8% operating margins, a 3.0–3.5x EV/Sales multiple is appropriate. That implies:

- EV = 3.25x × $710M = $2.31B
- Less net debt (~$157M) = equity value ~$2.15B
- Per share: ~$13.50

That is 45% downside from $24.58. It aligns precisely with the current analyst consensus target of $13.00. The market's sell-side already thinks this is overvalued — the stock is being held up by momentum, not consensus.

**Early warning signals to monitor:**
1. Q1 2026 revenue below $168M (low end of guide)
2. RPO growth decelerating below 30%
3. Security revenue growth falling below 25%
4. Net retention rate reversing back below 105%

**So what?** The stock's margin of safety is thin. A single quarter of deceleration could halve the stock price. This is not a resilient risk/reward setup for new money at current levels.

### 6.2 Secondary risks (thesis-weakening risks)

| Risk | Probability | Impact if occurs | Early warning signal | Why the bull case survives |
|------|------------|-----------------|---------------------|--------------------------|
| **Customer concentration** — top 10 = 34% of revenue. A single large customer loss would be a multi-percent revenue hit. | Medium | Revenue decline of 3–5% overnight. Gross margin compression as fixed costs spread over smaller base. | Watch quarterly top-10 concentration disclosure. Any increase above 36% is a red flag. | Non-top-10 customers grew 20% YoY. The base is diversifying, but slowly. |
| **ByteDance / TikTok regulatory risk** — ByteDance is a significant Fastly customer. The January 2026 US business restructuring resolved near-term uncertainty, but ongoing regulatory action could reduce traffic. | Medium | Loss of one of the top-10 customers. Revenue impact: estimated 3–4% of total revenue. | US government regulatory actions toward TikTok / ByteDance. Any new legislation or executive orders. | ByteDance restructured its US business in January 2026. Fastly's guidance now incorporates ByteDance revenue. |
| **Convertible debt maturity** — $188.6M of 0% notes due in 2026 must be settled. Cash settlement would consume over half of cash reserves. | High | Cash reserves drop from $362M to ~$173M. Limits financial flexibility for acquisitions or further CapEx. | Watch for refinancing announcements in 2026. If no refinancing by Q3, cash settlement is likely. | Fastly recently demonstrated capital market access (0% notes in Dec 2025). Refinancing is probable but not guaranteed in a volatile macro. |
| **Cloudflare competitive pressure** — Cloudflare spent ~$459M on R&D in FY2025 vs. Fastly's ~$180M. Cloudflare is building the same security + delivery + compute bundle at 2.5x the investment rate. | High | Fastly's performance differentiation narrows. Win rates decline. Growth decelerates. | Monitor Cloudflare's product launches in WAF, bot mgmt, and API security. If Cloudflare matches Fastly's sub-millisecond purging, the performance moat evaporates. | Fastly's 7-year Gartner Customers' Choice for WAAP suggests deep product-market fit that is not easily replicated — customers choose Fastly for a reason. |
| **Memory component cost inflation** — 25–75% YoY increases in memory components squeeze infrastructure CapEx and gross margin. | Medium-High | Gross margin compression of 100–200bps if component costs stay elevated. CapEx exceeds 12% of revenue. | Monitor DRAM spot pricing and Fastly's quarterly CapEx as % of revenue. | Fastly's software-defined architecture minimizes hardware dependency relative to legacy CDNs. The impact is real but manageable. |
| **CEO tenure risk** — Kip Compton has been CEO for <10 months. Fastly has had 3 CEOs in 5 years. | Low-Medium | Another CEO change would reset strategic direction and damage investor confidence, likely sending the stock to $10–12. | Watch for C-suite departures, especially if the CFO or CTO leave. Board turnover is also a signal. | Compton was promoted internally (CPO → CEO), suggesting board alignment. His Cisco pedigree and MIT/Wharton credentials are institutional-grade. |

### 6.3 Where the analogy breaks

The managed security services (MSS) analogy used in Module 1 breaks in three important ways:

1. **The dominant bundler already exists.** In the MSS transition, the cloud-native disruptors (Palo Alto, CrowdStrike) emerged after the bundling trend was established. In CDN, Cloudflare is already the cloud-native bundler — growing at 30%, with the broadest product suite and the largest developer ecosystem. Fastly is not disrupting the old guard; it is trying to keep pace with a faster competitor.

2. **Free-tier funnel dynamics have no MSS equivalent.** Cloudflare's free tier converts millions of small users into paying customers at near-zero acquisition cost. This creates a customer flywheel that Fastly cannot replicate — Fastly's go-to-market is enterprise-direct, which is higher-touch and higher-cost.

3. **Switching costs are lower.** In MSS, switching SIEM or managed security providers involved months of re-integration. In CDN, switching is a DNS change. While security products are stickier (WAF rules take time to tune), the underlying delivery layer is commoditized. Fastly's NRR of 110% is good but far below CrowdStrike's 130%+ — the stickiness is moderate, not entrenched.

### 6.4 Governance and management risks

No promoter disputes (Fastly has no controlling shareholder or founder-CEO dynamic). No material related-party transactions disclosed. No outstanding legal cases of thesis-level significance.

The governance concern is **leadership instability.** Three CEOs in five years (Bixby 2020–2022, Nightingale 2022–2025, Compton 2025–present) is unusual for a company of Fastly's size. Each transition disrupted go-to-market strategy and created organizational uncertainty. The board's decision to promote Compton internally — rather than conduct an external search — suggests urgency and a desire for continuity over transformation.

Stock-based compensation at ~19% of revenue ($120M+ annually) is elevated relative to peers and dilutes shareholders. SBC has exceeded net income in every year of the company's history. While this is common in growth-stage tech, Fastly is a 15-year-old company with $624M in revenue — the "growth-stage" excuse is wearing thin.

**So what?** The risks are real and specific. The thesis-killing risk (valuation compression on deceleration) is not hypothetical — the analyst consensus already targets $13. The secondary risks are manageable individually but correlated: a macro downturn could simultaneously decelerate growth, compress multiples, make debt refinancing expensive, and trigger customer budget cuts. Downside scenarios cluster, and the balance sheet does not provide a cushion against correlated shocks.

---

## Module 7: Valuation and final verdict

### 7.1 Strip out the noise — value only core earnings

To value Fastly's operating business honestly, strip out the cash, investments, and convertible debt:

| Component | Value |
|-----------|-------|
| Market cap | ~$3.8B |
| Plus: Total convertible debt | ~$518.6M |
| Less: Cash, equivalents, investments | ($362M) |
| **Enterprise Value** | **~$3.96B** |

| Valuation metric | Trailing (FY2025) | Forward (FY2026E) |
|------------------|------------------|-------------------|
| EV/Revenue | 6.3x | 5.6x |
| EV/Gross Profit (non-GAAP at 61%) | 10.4x | 8.8x |
| EV/Adj. EBITDA | 51.2x ($77.4M EBITDA) | ~36x ($110M est.) |
| EV/Non-GAAP Operating Income | 158x ($25M) | 72x ($55M) |

None of these multiples are "cheap" by any traditional standard. The only framework that makes Fastly look reasonably valued is a forward-looking model that assumes continued growth and margin expansion beyond FY2026.

### 7.2 Are you paying for the trough or the inflection?

Build a simple three-year earnings model:

| Metric | FY2025 (actual) | FY2026E (guided) | FY2027E (projected) | FY2028E (projected) |
|--------|----------------|------------------|--------------------|--------------------|
| Revenue | $624M | $710M | $840M (est. 18%) | $970M (est. 15%) |
| Gross margin | 60.9% | 63.0% | 64.5% | 65.5% |
| Operating margin | 4.0% | 8.0% | 11.0% | 13.0% |
| Operating income | $25M | $55M | $92M | $126M |
| Adj. EBITDA | $77M | $110M | $155M | $195M |

At $3.96B EV, the market is pricing:
- **FY2025 (trough):** 51x EBITDA — clearly not paying trough multiples
- **FY2026 (guided):** 36x EBITDA — expensive for a 14% grower
- **FY2027 (projected):** 26x EBITDA — reasonable IF 18% growth materializes
- **FY2028 (projected):** 20x EBITDA — fair value for a profitable, mid-teens grower

**The current stock price is paying for FY2027 earnings at a FY2027 multiple.** The market is two years ahead of the current financial reality. This means the stock works if — and only if — Fastly delivers 16–18% revenue growth AND 10%+ operating margins by FY2027. Any shortfall unwinds the valuation.

### 7.3 Downside protection — what is the floor?

**Bear case scenario:**
- Growth decelerates to 8–10% by FY2027 (macro slowdown, competitive losses)
- Operating margin stalls at 6% (CapEx inflation, pricing pressure)
- Market applies 3.0x EV/Sales (appropriate for a low-growth infrastructure company)

| Bear case | Value |
|-----------|-------|
| FY2027E revenue (bear) | $750M |
| EV at 3.0x sales | $2.25B |
| Less net debt (~$157M) | ($157M) |
| Equity value | $2.09B |
| Per share (~160M shares) | **~$13.00** |

**Bull case scenario:**
- Growth accelerates to 20%+ as AI traffic compounds and security portfolio matures
- Operating margin reaches 12% by FY2027
- Market applies 7.0x EV/Sales (premium platform multiple)

| Bull case | Value |
|-----------|-------|
| FY2027E revenue (bull) | $900M |
| EV at 7.0x sales | $6.3B |
| Less net debt | ($157M) |
| Equity value | $6.14B |
| Per share | **~$38.00** |

**Risk/reward at current price ($24.58):**
- Downside to bear case: -47% ($13)
- Upside to bull case: +55% ($38)
- Ratio: ~1.0x to 1.2x upside/downside

This is not compelling. A risk/reward ratio below 2:1 does not justify new capital deployment at current levels. The asymmetry improves significantly below $18 (where downside is ~28% vs. upside of ~111% — a 4:1 ratio).

### 7.4 The final verdict

**FAIRLY VALUED — with risk skewed to the downside at current prices.**

The turnaround is real. Fastly's security-driven cross-sell, RPO growth, margin inflection, and first profitable year are genuine accomplishments under a credible new management team. The company is executing the right strategy at the right time.

But the stock price has already rewarded this execution. A 400% rally from $5 to $25 in under 12 months has front-loaded years of expected returns. At 5.6x forward EV/Sales and 36x forward EV/EBITDA, the stock is pricing in FY2027 earnings that have not been earned yet. The margin of safety is negligible, and the analyst consensus ($13 target) suggests the buy-side is more bullish than the sell-side — a setup that historically corrects downward.

**Suggested approach:**

- **Short-term (3 months):** Hold if already owned. Do not initiate new positions above $22. The Q1 2026 earnings report (expected May 2026) is the near-term catalyst. A beat-and-raise sustains the rally; a guide-down could trigger a violent correction.
- **Long-term (3–5 years):** Accumulate on pullbacks below $18, where the risk/reward improves to 3:1+. At $18, you would be paying ~4.1x forward EV/Sales — a reasonable entry for a company with 14%+ growth and expanding margins.
- **Avoid above $28.** At that level, the stock prices in near-perfect execution through FY2028, with no margin for error.

**The one thing to monitor:** Q1 2026 security revenue growth. If security grows above 30% YoY in Q1, the cross-sell thesis is confirmed and the platform transformation is on track. If security decelerates below 25%, the Q4 strength was seasonal, and the thesis weakens materially. This single data point — one number, one quarter — will determine whether Fastly's inflection is durable or transient.

---

## Self-review checklist

| Check | Status |
|-------|--------|
| Does the report open with the INDUSTRY CYCLE, not the company history? | Yes — Module 1 covers CDN industry cycles before any company analysis. |
| Is there ONE CENTRAL THESIS that every module connects back to? | Yes — "security-driven cross-sell transforming Fastly from commodity CDN to platform, but stock already prices in the inflection." |
| Is the primary catalyst QUANTIFIED (in dollars, margin points, or percentage)? | Yes — RPO up 55% to $353.8M; security at 32% growth; gross margin from 56% → 64%; FCF swing of +$81.6M. |
| Is the industry analogy present — with both WHERE IT HOLDS and WHERE IT BREAKS? | Yes — MSS analogy with 5-point comparison table and 3 explicit breaks. |
| Is there a HEAD-TO-HEAD table with a clear verdict on who benefits more? | Yes — FSLY vs. NET vs. AKAM across 15 parameters with a direct verdict paragraph. |
| Does every risk section read like a BEAR CASE ANALYST wrote it — specific, quantified, and honest? | Yes — thesis-killing risk quantified to $13 downside. Six secondary risks with probability, impact, and early warning signals. |
| Is the thesis summarized in ONE SENTENCE at the top? | Yes — Module 0. |
| Have AI tailwinds been correctly weighted — are they the real driver, or is something else? | Yes — explicitly stated AI is the tailwind, not the ship. Security cross-sell is the primary driver. |
| Is there at least ONE direct management quote from the most recent earnings call? | Yes — multiple quotes from CEO Compton and CFO Wong throughout. |
| Is the valuation section showing EX-CASH / EX-INVESTMENTS multiples? | Yes — EV calculated stripping cash and adding debt. |
| Does the report end with a clear verdict AND one metric/event to monitor? | Yes — "FAIRLY VALUED." Monitor Q1 2026 security revenue growth (above/below 25–30%). |
| Is every paragraph free of padding, hedging, and filler phrases? | Yes — reviewed for "it is worth noting," "as mentioned above," and similar filler. None present. |

---

*Disclaimer: This report is for informational purposes only and does not constitute investment advice. The author may hold positions in the securities discussed. All data is sourced from public filings, earnings call transcripts, and third-party research as of March 2026. Past performance does not guarantee future results.*
