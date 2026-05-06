# 0. House‑Rules Prompt (Use Once Per Repo)

 You are assisting on the **HSBC Automated Service Review Pack (ASRP) — Phase 1 POC**. The customer is HSBC GPS. Treat the following as fixed context for everything you generate in this repo:

 *   Phase 1 only. No production hardening, no scheduled refresh, no Copilot Chat front end, no multi‑agent orchestration.
 *   Components on the Azure side (in scope here): **Azure AI Foundry, Azure AI Search, Azure OpenAI, Azure Function App (Python), Azure Blob Storage, Power Automate (HTTP trigger)**. The orchestrator is **Copilot Studio Agent**; the data store is **Dataverse**; the trigger is the **CSP Model‑Driven App**. These three are out of scope to build but are integration points.
 *   Language: **Python 3.11**. Azure Functions v4 programming model.
 *   Generation pattern: **RAG**. For each slide in the HSBC CSR template, the orchestrator calls AI Foundry with a **static per‑slide prompt** + grounded customer context retrieved by Azure AI Search over Dataverse content. Azure OpenAI returns a **JSON slide description** validated against a per‑slide schema.
 *   The slide loop is **sequential** in Phase 1.
 *   **Do not fabricate data.** When grounded data is missing, return `null` and add the field name to a `data_gaps` array.
 *   **Azure OpenAI is the only run‑time egress** from the HSBC application boundary. Never introduce other LLM providers.
 *   The Slide Creator is an Azure Function App in Python that uses **python‑pptx** and reads the **HSBC CSR template** from **Azure Blob Storage**.
 *   Authentication everywhere uses **Managed Identity** via `azure-identity` (`DefaultAzureCredential`). Never hard‑code secrets, keys, or connection strings.
 *   Code style: typed Python, `pydantic` models for all I/O, explicit error handling with structured logs, no print statements.

 When generating code, follow this context strictly. If something is ambiguous, ask once, then proceed with the safest assumption.