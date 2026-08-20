# FL-13: Agent Design Spec — Long-Horizon Idea Research & Validation Buddy

**Author:** Student / AI Engineer  
**Phase:** Build (Core) | **Target Build Hours:** 10 Hours  
**Framework:** Google Agent Development Kit (Google ADK - Python)  

---

## 🎯 1. Job To Be Done (JTBD) & User Persona

### The Job To Be Done
Build an **autonomous, continuous research and validation agent** that ingests raw project sparks (from Telegram notes, RSS feeds, Y-Combinator requests, or hackathon prompts), subjects each idea to a multi-agent **Red-Team debate** (Advocate vs. Critic vs. Feasibility Judge), verifies prior art via web search, prunes weak or oversaturated branches, and maintains a self-reflecting backlog of high-conviction build specs.

### User & Usage Frequency
* **Target User:** Solo AI Builder / Developer (the author).
* **Usage Frequency:** 
  * **Event-driven:** Ingests raw ideas on demand via Telegram/CLI.
  * **Scheduled/Continuous (24/7):** Runs background reflection loops every 6 hours ("sleeping pattern") to re-evaluate open research branches, prune dead ends, and deliver top-scored build specs.

---

## 🛠️ 2. Platform Choice & Technical Justification

### Selected Platform: **Scripted Python Agent using Google ADK**

| Feature | Selected Platform: Google ADK (Python) | Alternative 1: Claude Project / Custom GPT | Alternative 2: n8n Agent Workflow |
|---|---|---|---|
| **Autonomous Background Loops** | ✅ Full support for 24/7 background tasks & sleeping/reflection loops. | ❌ Requires manual user prompts in chat UI. Cannot sleep/cron. | ⚠️ Possible via cron nodes, but multi-turn agent debate is rigid. |
| **Model Diversity** | ✅ Supports pairing Gemini Flash (Advocate) with Gemini Pro (Critic). | ❌ Locked into single model provider context. | ⚠️ Supported, but complex to manage shared multi-turn memory state. |
| **Custom Memory & Branch Pruning** | ✅ Native Python state (SQLite / JSON graph memory). | ❌ Chat context window resets; no custom branch pruning logic. | ⚠️ Limited to standard vector DB or simple key-value stores. |
| **Cost & Budget Control** | ✅ Programmatic token & API call guardrails in code. | ❌ Fixed monthly subscription; no API cost controls. | ⚠️ Requires manual HTTP node rate limiting. |

**Justification:** Google ADK provides standard primitives for multi-agent delegation, structured tool calling, and long-horizon state management. Combined with Google's Gemini family (`gemini-3.7-flash` for high-velocity generation and `gemini-3.1-pro` for deep critical analysis), it offers an ideal foundation for a self-improving, budget-bounded research buddy.

---

## 🔌 3. Tools, Data Needed & Access Strategy

```
                          ┌────────────────────────────────┐
                          │    Google Gemini Models API    │
                          │ (Flash: Advocate / Pro: Critic)│
                          └───────────────▲────────────────┘
                                          │
┌───────────────────────┐         ┌───────┴────────┐         ┌───────────────────────┐
│ Ingestion Data Feeds  │────────►│ Google ADK Core│────────►│ Output & Storage      │
│ - Telegram Bot API    │         │ Agent Engine   │         │ - SQLite Memory DB    │
│ - YC / Hackathon RSS  │         └───────┬────────┘         │ - Telegram Alerts     │
└───────────────────────┘                 │                  └───────────────────────┘
                                  ┌───────┴────────┐
                                  │ Web Search Tool│
                                  │(Tavily / DDG)  │
                                  └────────────────┘
```

| Data / Tool | Purpose | Access Strategy / Credentials |
|---|---|---|
| **Google Gemini API** | Multi-agent LLM reasoning engine (`gemini-3.7-flash` & `gemini-3.1-pro`). | Access via `GEMINI_API_KEY` environment variable. |
| **Google ADK (Python)** | Framework for multi-agent orchestration & tools. | Installed via PyPI (`google-adk` / `google-genai`). |
| **Web Search API** | Prior-art search (GitHub repos, ProductHunt, papers). | Tavily API key (`TAVILY_API_KEY`) or fallback to DuckDuckGo Python SDK. |
| **Telegram Bot API** | Ingestion of raw user sparks & sending high-conviction alerts. | Telegram Bot Token (`TELEGRAM_BOT_TOKEN`) via `python-telegram-bot`. |
| **SQLite Memory Store** | Long-horizon state, research trees, and reflection logs. | Local filesystem storage (`/data/research_memory.db`). |

---

## 🤖 4. Agent Architecture & System Instructions

### Multi-Agent Debate Design (Model Diversity)

To eliminate single-model confirmation bias, the system uses 3 distinct agent roles:

```
                  [Raw Idea Spark]
                         │
                         ▼
             ┌──────────────────────┐
             │  1. Advocate Agent   │  (Model: Gemini Flash)
             │  "Why this is great" │
             └───────────┬──────────┘
                         │
                         ▼
             ┌──────────────────────┐
             │   2. Red-Team Critic │  (Model: Gemini Pro)
             │  "Why this will fail"│
             └───────────┬──────────┘
                         │
                         ▼
             ┌──────────────────────┐
             │ 3. Feasibility Judge │  (Model: Gemini Pro + Web Search)
             │ Score (1-10) & Prune │
             └──────────────────────┘
```

### System Instructions Draft

```markdown
Role: Autonomous Long-Horizon Idea Research & Validation Agent (Google ADK)

Core Objectives:
1. Ingest raw or vague idea sparks, debate pros/cons, and actively discover unserved market/technical niches.
2. Conduct a 3-stage internal debate & prior-art verification:
   - Advocate (Gemini Flash): Brainstorm utility, target users, candidate niches, and unique value propositions.
   - Red-Team Critic (Gemini Pro): Challenge assumptions, identify failure modes, cost traps, and existing oversaturated solutions.
   - Feasibility Judge (Gemini Pro + Web Search Tool): Search GitHub, ProductHunt, and papers for existing solutions. Identify missing features/flaws in prior art and calculate Feasibility (1-10) and Novelty (1-10) scores.
3. Execute long-horizon reflection & spark enrichment: Periodic sleeping loop reads past research memory, prunes branches scored < 6/10, enriches vague sparks into concrete 3-hour MVP specs, and flags unique niches.

Operational Rules:
- NEVER execute code experiments or deploy services automatically.
- Output strictly structured Markdown summaries with clear [BUILD], [PARK], or [PRUNE] recommendations, complete with Pros/Cons and Niche Analysis.
```

---

## 🧪 5. Pre-Build Evaluation Suite (5 Eval Cases)

| ID | Test Input | Expected Agent Tool & Debate Flow | Pass Criteria / Expected Result |
|---|---|---|---|
| **E-01** | **Oversaturated Idea:** *"Build an AI tool that summarizes unread Gmail emails"* | Feasibility Judge calls Web Search tool; finds 50+ commercial products. Red-Team Critic highlights zero differentiation. | **Outcome:** Status `PRUNE` (Novelty: 2/10). Logged to SQLite memory without alerting user. |
| **E-02** | **Technically Flawed Idea:** *"Build a local LLM on Raspberry Pi that accurately predicts stock market prices with 99% accuracy"* | Critic flags financial and hardware impossibility. Advocate fails to produce viable defense. | **Outcome:** Status `PRUNE` (Feasibility: 1/10). Clear technical rationale recorded. |
| **E-03** | **High-Conviction Niche Idea:** *"Long-Horizon Research Buddy with Red-Team Debate using Google ADK & sleeping loops"* | Advocate highlights high relevance for Google Hackathon; Critic challenges 10-hour build scope; Judge verifies open-source components (`google/adk-samples`). | **Outcome:** Status `BUILD` (Feasibility: 8/10, Novelty: 9/10). Formatted 1-page spec sent to Telegram. |
| **E-04** | **Vague / Underspecified Spark:** *"Something with AI and PDF reporting"* | Advocate proposes 3 angles (invoice extraction, streaming PDF reports, dynamic charts); Judge checks web for prior art; Critic eliminates basic summarizers and pinpoints *Streaming PDF Generation with Concurrency Locks* as the unserved niche. | **Outcome:** Status `ENRICHED_NICHE_PROPOSAL`. Returns Pros/Cons analysis, 3 competitor gaps, and a refined build proposal to Telegram. |
| **E-05** | **Daily Budget Limit Breach:** *Agent triggers 100 continuous research loops in an automated loop* | Rate Limiter / Budget Control interceptor checks daily token counter vs `$2.00` cap. | **Outcome:** Agent halts execution gracefully, commits state to SQLite, and alerts Telegram: *"Daily budget limit reached."* |

---

## 🛡️ 6. Risks, Guardrails & Human-in-the-Loop Controls

### What the Agent Must NEVER Do (Forbidden Actions)
1. **No External Publishing or Deployment:** The agent must NEVER post directly to LinkedIn, Twitter, GitHub, or trigger server deployments without explicit human approval.
2. **No Unbounded Web Crawling:** Web search queries are strictly capped at depth = 2 and maximum 5 search calls per idea evaluation.
3. **No Uncapped API Spend:** The agent must NEVER execute LLM calls if the daily estimated token cost exceeds `$2.00 USD`.

### What the Agent MUST Confirm (Human-in-the-Loop)
* **Spec Promotion to Build:** When an idea scores $\ge 8/10$, the agent prepares a draft MVP specification and sends a Telegram message with `[Approve Build]` / `[Reject]` interactive buttons.
* **Architecture Deviations:** If research reveals the initial tech stack must change (e.g. switching DBs or APIs), the agent highlights this in the review draft.

---

## ⏱️ 7. Build Scope Breakdown (~10 Build Hours)

| Phase | Task Description | Est. Hours |
|---|---|---|
| **Phase 1: Setup & ADK Foundation** | Initialize Google ADK Python environment, setup `gemini-3.7-flash` and `gemini-3.1-pro` connections, SQLite schema. | 2.0 hrs |
| **Phase 2: Multi-Agent Debate Pipeline** | Build Advocate, Red-Team Critic, and Feasibility Judge agents with model diversity. | 3.0 hrs |
| **Phase 3: Web Search & Memory Loop** | Implement Tavily/DuckDuckGo search tool integration and long-horizon sleeping/reflection loop. | 2.5 hrs |
| **Phase 4: Telegram Bot & Guardrails** | Build Telegram ingestion/alerting handler, rate limiter, and budget control hooks. | 1.5 hrs |
| **Phase 5: Verification & Eval Suite** | Run the 5 eval cases, record results, refine prompts. | 1.0 hr |
| **Total** | | **10.0 hrs** |

---

## 🏁 Conclusion

This design doc outlines a high-impact, realistic personal AI agent that directly addresses idea evaluation friction. By choosing **Google ADK with model diversity** (Flash for speed, Pro for critique), enforcing strict **budget guardrails**, and defining **5 concrete pre-build evals**, the project is primed for a successful 10-hour build.
