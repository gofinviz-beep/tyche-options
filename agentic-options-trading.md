# Architecture Design Document  
## Laptop-Based Options Trading Copilot (MVP)

**Document Version:** 1.1  
**Status:** Draft  
**Audience:** Personal use, design review, future implementation planning  
**Primary Deployment Model:** Local laptop execution, with future GCP service deployment  
**Broker Model:** Official public brokerage API only  
**Initial Broker Target:** Tradier  
**Secondary Broker Option:** Interactive Brokers (future portability)

---

## 1. Executive Summary

This document defines the architecture for a laptop-based **options trading copilot** that uses a brokerage provider’s **official public APIs** and official market data to support legal, controlled, and traceable options trading.

The system is intended to function as a **semi-automated decision-support and execution-assist application** for a personal brokerage account. Its purpose is to improve discipline, speed, visibility, and consistency in the daily options trading workflow by combining:

- broker-integrated account and market data
- deterministic screening and risk rules
- LLM-assisted analysis and ranking
- human approval before live order execution
- local journaling and trade memory

This design intentionally avoids the unsafe and non-compliant elements seen in some informal AI trading setups, including reverse-engineered mobile applications, private broker APIs, uncontrolled autonomous execution, and opaque risk handling.

The first version will run entirely on a personal laptop, prioritizing simplicity, observability, and manual control over scale or autonomy. The architecture also includes a clear migration path to **Google Cloud Platform (GCP)** so the application can later run as a managed service.

---

## 2. Objectives

### 2.1 Business Objective
Create a practical personal trading copilot that helps identify, evaluate, and manage options trades with enough consistency to potentially generate side income while operating within legal and broker-approved boundaries.

### 2.2 Technical Objectives
The application must:

- connect to a broker through an official public API
- retrieve account balances, positions, open orders, quotes, and options chains
- scan a curated watchlist for valid options opportunities
- apply deterministic filters before invoking the LLM
- use the LLM for summarization, comparison, ranking, and rationale
- preview proposed trades before any live submission
- require explicit human approval for all live orders
- record all decisions, recommendations, approvals, and outcomes locally
- remain portable to a future cloud deployment on GCP

### 2.3 Operating Objective
The system should reduce emotional trading behavior by turning ad hoc decision-making into a repeatable daily workflow driven by rules, structured recommendations, and portfolio awareness.

---

## 3. Non-Goals

The MVP explicitly excludes the following:

- reverse engineering broker applications
- using private or undocumented APIs
- fully autonomous live trading
- high-frequency or low-latency trading
- naked short options strategies
- cross-account or multi-user support
- cloud deployment or distributed execution in the first release
- selling signals or advice to third parties
- acting as an investment adviser or any external advisory service

---

## 4. Assumptions

The architecture assumes the following:

1. The user has an approved retail brokerage account with options permissions.
2. The broker offers public API support for account data, option chains, and order submission.
3. The application is used only for the user’s own account and not for managing money for others.
4. The user is willing to start with a **semi-automated** model where every live trade requires approval.
5. The user can maintain a curated watchlist instead of expecting the LLM to scan the entire market without guardrails.
6. The laptop will remain powered on during scheduled scans and reviews.
7. The user accepts that the LLM is advisory and interpretive, not a guaranteed source of profitable signals.
8. Strategy performance is not assumed; the initial priority is safe workflow and controlled experimentation.
9. The future GCP deployment will continue to serve only the user’s personal account unless the scope is explicitly expanded.

---

## 5. Design Principles

The system will follow these architectural principles:

### 5.1 Legal by Design
Every broker interaction must use an official public API and operate within published broker terms and permissions.

### 5.2 Human-in-Control
No live trade will be submitted without explicit human approval in the MVP.

### 5.3 Rules Before Language
Deterministic code will enforce all hard constraints. The LLM may recommend, explain, and rank, but may not override risk or policy rules.

### 5.4 Narrow Scope First
The system will begin with a small set of allowed strategies, a limited watchlist, and modest daily workflow frequency.

### 5.5 Local First, Cloud Ready
The MVP will run on a laptop using local storage and local scheduling to keep complexity and cost low, while preserving portability to GCP.

### 5.6 Auditability
Every recommendation, rule decision, preview, approval, submission, and outcome must be traceable through local logs and structured records.

### 5.7 Replaceable Broker Layer
Broker-specific logic must be isolated so that the application can later support another compliant broker if needed.

### 5.8 Separation of Operational and Analytical Data
The system should keep transactional state separate from long-term analytical history so that live trading workflows remain simple and reliable.

---

## 6. System Scope

The system is a laptop-hosted options trading copilot with five core functions:

1. **Broker Connectivity**  
   Retrieve balances, positions, orders, quotes, and options chains; submit, cancel, and replace orders.

2. **Market Scanning**  
   Evaluate a predefined watchlist and identify valid trade candidates based on deterministic filters.

3. **Trade Analysis**  
   Use the LLM to compare, rank, and explain the strongest candidates and position-management actions.

4. **Risk Control**  
   Apply hard constraints to capital usage, allowed strategies, position sizing, and exposure limits.

5. **Execution Support**  
   Generate previews, present recommendations, and submit live orders after user approval.

---

## 7. High-Level Architecture

```text
+------------------------------------------------------------------+
|                     Options Trading Copilot                      |
|                   (Laptop First, GCP-Ready Design)              |
+------------------------------------------------------------------+
| User Interface Layer                                             |
| - Dashboard                                                      |
| - Candidate review                                               |
| - Position review                                                |
| - Order preview / approval                                       |
| - Journal / history                                              |
+------------------------------------------------------------------+
| Workflow Orchestration Layer                                     |
| - Morning scan                                                   |
| - Midday portfolio review                                        |
| - End-of-day journal                                             |
+------------------------------------------------------------------+
| Analysis Layer                                                   |
| - Strategy engine                                                |
| - Signal / ranking engine                                        |
| - LLM reasoning and summarization                                |
| - Exit ladder generation                                         |
+------------------------------------------------------------------+
| Risk & Controls Layer                                            |
| - Position sizing                                                |
| - Exposure validation                                            |
| - Allowed strategy checks                                        |
| - Max loss and concentration checks                              |
| - Kill switch / preview-only enforcement                         |
+------------------------------------------------------------------+
| Broker & Market Data Layer                                       |
| - Account balances                                               |
| - Positions / open orders                                        |
| - Quotes / chains / contract metadata                            |
| - Order preview / place / cancel / replace                       |
+------------------------------------------------------------------+
| Persistence & Storage Layer                                      |
| - SQLite locally for MVP                                         |
| - Cloud SQL PostgreSQL for future operational state              |
| - BigQuery for future analytics and history                      |
+------------------------------------------------------------------+
````

---

## 8. Component Architecture

### 8.1 User Interface Layer

The user interface is the primary control surface for the system. It should be minimal but operationally effective.

#### Responsibilities

* display balances, buying power, positions, and P&L
* show candidate trades with explanations and confidence
* show open orders and repricing suggestions
* present order previews before submission
* require explicit approval for live orders
* present historical decisions and outcomes

#### MVP Characteristics

* local-only
* simple local web app or CLI-backed UI
* low operational overhead
* optimized for daily review, not continuous monitoring

---

### 8.2 Workflow Orchestration Layer

This layer coordinates the timed workflows and manual workflows that define the user’s trading day.

#### Core Jobs

* **Morning Scan:** build the daily candidate set
* **Midday Review:** review open positions and open orders
* **End-of-Day Journal:** persist observations, snapshots, and lessons

#### Responsibilities

* trigger data refreshes
* trigger filtering and ranking workflows
* trigger journaling and snapshot routines
* enforce whether the app is in preview-only or live-approved mode

---

### 8.3 Broker & Market Data Layer

This layer encapsulates all interactions with the official brokerage API.

#### Responsibilities

* retrieve balances, buying power, and account summary
* retrieve current positions and open orders
* retrieve real-time underlying quotes
* retrieve options chains and contract metadata
* request order previews
* place, cancel, and replace orders

#### Design Requirement

All broker responses must be normalized into internal data models so that upstream logic is not tightly coupled to one broker’s response format.

---

### 8.4 Strategy Engine

The strategy engine implements deterministic screening logic.

#### Responsibilities

* scan a curated watchlist
* eliminate invalid or low-quality setups
* apply liquidity and tradability rules
* score candidates using deterministic criteria

#### Example Constraints

* liquid underlyings only
* minimum contract open interest
* minimum contract volume
* acceptable spread width
* allowed DTE range
* strategy whitelist only
* avoid contracts near major binary events unless explicitly enabled

This layer is intentionally independent of the LLM.

---

### 8.5 LLM Analysis Layer

The LLM layer acts as an interpretive and comparative system rather than a free-form trading engine.

#### Responsibilities

* summarize catalyst and market context
* compare top filtered candidates
* rank candidates using structured inputs
* explain why a trade is attractive or unattractive
* suggest entry zones, targets, stop logic, and exit ladders
* review current positions and propose hold / trim / exit actions

#### Constraint

The LLM is advisory only. It cannot approve its own trade, submit a live order on its own, or bypass any deterministic rule.

---

### 8.6 Risk & Controls Layer

This is the primary safety layer and must remain entirely deterministic.

#### Responsibilities

* enforce max risk per trade
* enforce max account exposure
* enforce max per-ticker concentration
* enforce allowed strategy types
* enforce max number of open positions
* enforce max new trades per day
* enforce order preview requirement
* disable live trading when preview-only mode is active

#### Core Principle

If the LLM recommends something outside policy, the system rejects it automatically and explains why.

---

### 8.7 Persistence Layer

The persistence layer provides continuity, journaling, auditability, and learning support.

#### MVP Storage Approach

SQLite is sufficient for the first laptop-based implementation.

#### Future Cloud Storage Approach

For GCP deployment, the system will use:

* **Cloud SQL for PostgreSQL** as the primary operational database
* **BigQuery** as the analytical and historical store

#### Responsibilities

* store account snapshots
* store trade candidates and recommendations
* store approvals, rejections, and execution results
* store bot memory and rationale history
* maintain a clear journal of recommendation-to-outcome chains

---

## 9. Data Storage Strategy

### 9.1 MVP Local Storage

For the laptop-based MVP, the application will use **SQLite** as the local database because it is lightweight, easy to manage, and sufficient for a single-user local application.

SQLite will store:

* account snapshots
* open positions
* open orders
* watchlist metadata
* option candidates
* trade recommendations
* approvals and rejections
* execution history
* bot memory
* daily journal entries

The local schema should remain as close as practical to PostgreSQL-compatible design so that migration to Cloud SQL is straightforward later.

### 9.2 Target GCP Operational Database

For the GCP service deployment, the primary application database will be **Cloud SQL for PostgreSQL**.

Cloud SQL will store the system’s **transactional and operational state**, including:

* current positions
* open orders
* live recommendation state
* risk settings and configuration
* approval decisions
* execution audit logs
* bot notes and memory
* user and broker metadata

Cloud SQL is preferred because the application is heavily relational and transactional, requiring:

* consistency
* referential integrity
* structured queries
* easy auditability
* strong support for workflows involving orders, previews, approvals, and execution records

### 9.3 Target GCP Analytical Database

For long-term analytics and history, the system will use **BigQuery**.

BigQuery will store:

* historical account snapshots
* historical recommendation outputs
* execution performance over time
* daily P&L and attribution records
* trade outcome analytics
* historical chain snapshots, if retained
* research datasets for later strategy evaluation

BigQuery is not intended to serve live trading state. It is intended for:

* analytics
* dashboards
* backtesting support
* long-term performance analysis
* feature exploration and historical research

### 9.4 Storage Separation Principle

The design intentionally separates:

* **operational state** in SQLite/Cloud SQL
* **analytical history** in BigQuery

This reduces risk in live workflows and keeps execution-sensitive paths simple, reliable, and easy to reason about.

---

## 10. Data Model Overview

The following logical entities are recommended:

### 10.1 AccountSnapshot

Stores time-stamped account state including:

* cash
* buying power
* net liquidation value
* margin-related figures, if applicable

### 10.2 Position

Stores position details including:

* underlying symbol
* contract metadata
* quantity
* average cost
* unrealized P&L
* open date
* strategy classification

### 10.3 OpenOrder

Stores:

* order type
* contract
* price
* status
* time submitted
* source of decision

### 10.4 WatchlistSymbol

Stores:

* ticker
* category or theme
* tradability flags
* liquidity preferences

### 10.5 OptionCandidate

Stores:

* contract identifier
* strike and expiry
* spread metrics
* volume / OI
* DTE
* rank inputs
* rule-pass status

### 10.6 TradeRecommendation

Stores:

* recommendation type
* thesis
* confidence
* entry suggestion
* target suggestion
* stop logic
* exit ladder proposal

### 10.7 ExecutionDecision

Stores:

* approved or rejected
* timestamp
* user note
* preview result summary

### 10.8 TradeJournal

Stores:

* full recommendation-to-outcome chain
* realized result
* user action history
* post-trade notes

### 10.9 BotMemory

Stores brief running notes such as:

* why a position exists
* why a prior trade was rejected
* what recent changes were made
* current portfolio context for later reviews

---

## 11. Daily Operating Workflow

### 11.1 Morning Scan Workflow

1. Load account balances, positions, and open orders
2. Load quotes and option chains for the watchlist
3. Apply deterministic filters
4. Rank candidates using rule-based scores
5. Send the best candidates to the LLM
6. Receive ranked recommendations with rationale and exit guidance
7. Present 3–5 top ideas to the user

### 11.2 Trade Approval Workflow

1. User selects a recommendation
2. System calculates allowed position size
3. System validates against exposure and strategy rules
4. System requests order preview
5. System presents preview, risk summary, and rationale
6. User approves or rejects
7. If approved, the order is submitted and logged

### 11.3 Midday Position Review

1. Refresh positions and open orders
2. Review unrealized P&L and recent movement
3. Ask the LLM to identify exits, trims, or repricing opportunities
4. Present only rule-compliant actions

### 11.4 End-of-Day Journal

1. Save account and position snapshot
2. Save recommendation and approval outcomes
3. Record user notes and system observations
4. Build next-day continuity context

---

## 12. Grounding Rules for the Agent / LLM

The LLM must operate under explicit grounding rules so that recommendations remain bounded, verifiable, and safe.

### 12.1 Source Grounding Rules

The agent must base its reasoning only on:

* broker-provided account data
* broker-provided quote and options chain data
* approved watchlist inputs
* deterministic strategy outputs
* stored local trading history
* explicitly permitted public context sources, if later added

The LLM must not invent:

* positions
* balances
* buying power
* fills
* bid/ask prices
* Greeks
* expiration dates
* order statuses

If required market fields are missing, the LLM must state that the recommendation is incomplete.

### 12.2 Decision Boundary Rules

The LLM may:

* summarize
* rank
* explain
* compare
* suggest entries and exits
* suggest order structures
* explain trade-offs between alternatives

The LLM may not:

* place live orders without human approval
* override deterministic risk rules
* invent confidence unsupported by inputs
* recommend prohibited strategies
* bypass preview requirements
* alter watchlist scope unless explicitly instructed

### 12.3 Risk Communication Rules

For every recommendation, the LLM should explicitly state:

* why the trade is being considered
* what invalidates the thesis
* what the likely holding period is
* what key risks are present
* whether the trade depends on momentum, mean reversion, event risk, or volatility expansion/contraction

### 12.4 Portfolio Awareness Rules

The LLM must consider:

* existing positions in the same ticker
* overlapping directional exposure
* concentration by sector or theme, if available
* whether a similar bot trade was recently entered
* whether capital is already reserved by open orders

### 12.5 Output Format Rules

The LLM should produce structured outputs, including:

* trade candidate
* direction
* contract details
* thesis
* entry guidance
* target / exit ladder
* invalidation point
* confidence
* risks
* reason for rejection if no trade is recommended

### 12.6 Escalation Rules

If the data is noisy, contradictory, stale, or incomplete, the LLM must prefer:

* “no trade”
* “need refreshed market data”
* “hold existing position”
  over speculative recommendations.

### 12.7 Hallucination Guardrails

The system prompt and runtime checks should instruct the LLM:

* do not assume missing data
* do not describe an order as placed unless a confirmed broker response exists
* do not state a position exists unless it is present in broker data
* do not restate a previous day’s thesis as current unless fresh data confirms it
* do not confuse preview status with execution status

---

## 13. Security Considerations

The application will handle brokerage credentials and trading decisions, so local and cloud security must be taken seriously.

### 13.1 Secrets Management

* credentials must not be hard-coded
* environment variables or secure local secrets storage should be used in MVP
* cloud deployment should use **GCP Secret Manager**
* logs must never expose secrets

### 13.2 Safety Controls

* default startup mode should be preview-only
* live trading must require explicit enablement
* a manual kill switch must be available
* all live actions must be logged

### 13.3 Account Segmentation

The initial deployment should use:

* a dedicated small-capital brokerage account for experimentation
* limited risk and limited allowed strategies

### 13.4 Cloud Access Controls

In GCP deployment:

* service identities should be least-privileged
* Cloud SQL access should be application-restricted
* analytical datasets should be permissioned separately from operational data
* production secrets and development secrets should be separated

---

## 14. Target GCP Deployment Architecture

The initial system will run locally, but the target service deployment on GCP is as follows.

### 14.1 Primary GCP Services

* **Cloud Run** for the main application service and API
* **Cloud SQL for PostgreSQL** for operational database state
* **BigQuery** for analytics and long-term history
* **Secret Manager** for broker and LLM credentials
* **Cloud Scheduler** for timed jobs such as morning scans and end-of-day journaling

### 14.2 Optional Future Services

* **Pub/Sub** for asynchronous event-driven workflows
* **Cloud Logging / Monitoring** for operational visibility
* **Artifact Registry** for container images

### 14.3 GCP Deployment Model

In the service-based version:

* UI/API runs on Cloud Run
* scheduled scan/review jobs are triggered by Cloud Scheduler
* application state is stored in Cloud SQL
* historical and analytical data is copied to BigQuery
* secrets are retrieved securely from Secret Manager

### 14.4 GCP Design Goals

The GCP deployment should preserve the same control model as the laptop version:

* deterministic risk rules remain in application code
* LLM remains advisory
* live order placement still requires explicit approval unless policy changes later
* operational simplicity remains a priority

---

## 15. Risks

### 15.1 Strategy Risk

The system may be operationally sound but financially unprofitable.

### 15.2 Data Risk

Stale, incomplete, or low-quality market data can produce poor recommendations.

### 15.3 LLM Reasoning Risk

The LLM may produce persuasive but weak reasoning if not tightly grounded.

### 15.4 Execution Risk

Order previews may differ from realized fills due to market movement and slippage.

### 15.5 Operational Risk

A laptop-based system may miss jobs if asleep, disconnected, or shut down. A cloud deployment reduces but does not eliminate operational risk.

### 15.6 Human Override Risk

The user may ignore the system’s constraints or overrule prudent recommendations.

### 15.7 Regulatory Risk

Even for personal use, the system must remain within broker terms and retail account permissions.

### 15.8 Cost Drift Risk

If the system later stores excessive raw market data or invokes the LLM too frequently, cloud storage and inference costs may grow unnecessarily.

---

## 16. Open Questions

The following design questions remain open for implementation planning:

1. What exact watchlist universe should the MVP support?
2. What is the first allowed strategy set?
3. What is the maximum risk per trade and per day?
4. Should earnings-related trades be allowed at all in the MVP?
5. What confidence rubric should the LLM use?
6. What is the preferred user interface: Streamlit, FastAPI-based local app, or CLI-first?
7. What external context sources, if any, should be added later?
8. Should paper trading run in parallel with live-approved trading for comparison?
9. How much historical trade journaling is needed before tuning the ranking logic?
10. What exact criteria should define “no trade today”?
11. How much chain and quote history should be retained in Cloud SQL vs BigQuery?
12. Should market snapshots be persisted only for traded symbols or for the full watchlist?

---

## 17. Future Enhancements

The following are appropriate after the MVP is stable:

### 17.1 Broker Portability

Add a second compliant broker integration such as IBKR.

### 17.2 Paper vs Live Comparison

Run recommendations in paper mode even when live trades are not approved, to compare decision quality.

### 17.3 Broader Signal Inputs

Add approved external public data such as earnings calendars, macro events, or sentiment inputs.

### 17.4 Strategy Specialization

Split the engine into separate playbooks, such as:

* momentum long calls
* bearish long puts
* defined-risk spreads
* volatility event trades

### 17.5 Smarter Portfolio Management

Add portfolio-level optimization and capital reallocation recommendations.

### 17.6 Better Memory

Use structured historical outcome analysis to improve ranking and avoid repeated low-quality trade patterns.

### 17.7 Deployment Hardening

Move from laptop-only execution to a more reliable hosted environment after the workflow is proven.

### 17.8 Analytical Dashboards

Add BigQuery-backed performance dashboards for:

* realized/unrealized P&L
* hit rate by setup type
* average hold duration
* recommendation-to-execution conversion
* LLM recommendation quality over time

---

## 18. MVP Recommendation

The MVP should remain intentionally narrow:

* local laptop deployment only
* one broker
* one curated watchlist
* one or two allowed strategy types
* morning scan + midday review + end-of-day journal
* mandatory human approval for all live orders
* deterministic risk engine with LLM advisory support only
* SQLite locally, with schema portability to Cloud SQL

This design captures the useful ideas from modern AI-assisted trading workflows:

* continuous structured review
* portfolio-aware recommendations
* exit planning
* persistent trade memory
* lower emotional interference

At the same time, it avoids the most dangerous patterns:

* illegal access methods
* black-box autonomy
* unbounded market scanning
* uncontrolled leverage
* poor auditability

---

## 19. Success Criteria

The MVP will be considered successful if it can reliably:

1. connect to the broker and retrieve the required account and market data
2. produce a small set of valid daily options candidates
3. reject rule-breaking ideas automatically
4. generate useful, grounded trade rationale and exit suggestions
5. preview and submit approved orders correctly
6. maintain an auditable history of recommendations, decisions, and outcomes

In the early phase, success should be measured primarily by:

* control
* consistency
* traceability
* usability
* quality of decision support

Profitability should be treated as a later outcome of disciplined iteration, not the first proof of success.

