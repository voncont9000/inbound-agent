# Customer context graph: research and recommendation

| | |
|---|---|
| Status | Proposal v0.1 |
| Related | [PRD](./PRD.md) §7.10, [Fonio teardown](./fonio-teardown.md) |

## 1. What we need
Every interaction (call, email, WhatsApp message) has to connect to the right **customer** and their **organisation**. It also has to connect to what was said, promised and decided. The agent on any channel can then answer questions like "this is the person who emailed about a wholesale quote on Tuesday and promised to pay invoice 1042 by Friday."

Requirements derived from the PRD decisions (UK, multi-tenant SaaS, drafts first, own voice stack):

| # | Requirement | Why |
|---|---|---|
| R1 | **Identity resolution** across phone number (E.164), email address and WhatsApp ID, with confidence levels and merge/split | The same person shows up under different identifiers on each channel |
| R2 | **Typed entities and relationships**: person, organisation, conversation, message, topic/case, task, commitment, invoice/order, product | Answers and follow-ups depend on structure, not only text similarity |
| R3 | **Temporal facts with provenance**: every fact knows when it became true, when it was superseded, and which message it came from | Facts go out of date (new address, paid invoice). We must show *why* the agent believes something |
| R4 | **Low-latency context load**: the customer context is in the prompt before the voice greeting | Voice budget is p95 ≤ 1.5 s per turn. Lookups must happen while the call is ringing |
| R5 | **Hard tenant isolation** | Multi-tenant SaaS. A leak between businesses would be fatal |
| R6 | **UK GDPR**: per-person export and erasure, retention limits, purpose limitation | Erasure must remove the person *and* every fact derived from their messages |
| R7 | **Extensible to business data**: invoices and payments from Xero/QuickBooks, orders, bookings | Needed for later features such as chasing outstanding payments |
| R8 | **Auditability**: human-visible, correctable | Owners must be able to fix a wrong fact or a wrong merge |

## 2. Options considered

| Option | What it is | Strengths | Weaknesses for us |
|---|---|---|---|
| **A. Postgres-native typed graph** (tables for entities and edges, recursive CTEs, pgvector) | Our system of record also stores the graph | One database. Transactions shared with conversations. Row-level security (RLS) for tenancy. GDPR erasure in one place. TypeScript-friendly. Customer-centric queries are 1–3 hops, which SQL handles well | We build extraction, dedup and fact invalidation ourselves. Deep or ad-hoc graph analytics are awkward |
| **B. Graphiti** (Zep's open-source temporal KG engine, Apache-2.0, Python) on Neo4j / FalkorDB / Neptune / Kuzu | LLM pipeline that turns "episodes" (messages, JSON) into entities and bi-temporal facts with provenance. Hybrid search (semantic, keyword, graph) | Purpose-built for agent memory. Handles fact invalidation over time. `group_id` namespaces. Published benchmark results (DMR 94.8%, LongMemEval gains)¹ | Python sidecar plus a second datastore to secure, sync and erase. LLM cost and latency per ingested episode. Graphiti's docs say `group_id` is not an authorisation boundary: the app must enforce it |
| **C. Zep Cloud** (managed Graphiti) | Hosted memory API | Fastest to start | Customer PII leaves our infrastructure. Vendor lock-in. UK data-residency and DPA questions |
| **D. Mem0** (graph memory) | Memory layer: entities extracted from memories and linked | Simple API | Memory-centric ("facts about a user"), not a business-entity model. Graph results add context but do not change ranking² |
| **E. Neo4j / FalkorDB as primary store** | Dedicated graph database | Expressive Cypher, fast traversal. FalkorDB offers graph-per-tenant³ | Data duplicated with Postgres. Neo4j Community has one DB per instance, so multi-DB tenancy needs Enterprise⁴. Licence checks needed |
| **F. Apache AGE** (Cypher inside Postgres) | Graph extension for Postgres | Cypher without a second database | Smaller project. PG18 support was still in progress in early 2026⁵. Not available on most managed Postgres services |
| **G. Microsoft GraphRAG** | Batch community summarisation over a static corpus | Good for "global" questions over documents | Built for static data. Updates trigger recomputation. Multi-call retrieval is slow⁶. Not suited to live conversations |

¹ [Zep paper](https://arxiv.org/abs/2501.13956) · ² [Mem0 graph memory docs](https://github.com/mem0ai/mem0/blob/v1.0.10/docs/open-source/features/graph-memory.mdx) · ³ [FalkorDB multi-tenancy](https://docs.falkordb.com/cloud/features) · ⁴ [Neo4j DB administration](https://neo4j.com/docs/operations-manual/current/database-administration/) · ⁵ [AGE discussion #2305](https://github.com/apache/age/discussions/2305) · ⁶ [Neo4j blog: Graphiti vs GraphRAG](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/) · [Graphiti namespacing](https://help.getzep.com/graphiti/core-concepts/graph-namespacing.md)

## 3. Recommendation
**Build a Postgres-native, bi-temporal "customer context graph" (Option A), modelled on Graphiti's design.** Keep the graph behind a `ContextGraph` interface. Run a spike in parallel that tests Graphiti (Option B) as the *extraction engine*, writing its output into our Postgres model.

Reasons:
1. **The questions are customer-centric**: "everything about this caller", "open commitments of this organisation". These are ego-graph lookups of 1–3 hops from an identity. Postgres with good indexes answers them in milliseconds. A separate graph database helps most with deep or global traversals, which we don't need.
2. **Tenancy and GDPR are the hard parts.** One store with RLS on `tenant_id` and a single erasure path is much safer than keeping Postgres and a graph store consistent.
3. **The useful ideas from Graphiti are design patterns, not infrastructure.** Episodes → entities → facts with `valid_from/valid_to` and provenance can all be implemented as tables.
4. **Structured business data comes first.** Invoices, orders and bookings are relational. A typed schema with an extensible `fact` table beats a fully schema-less graph here.
5. **Escape hatch.** If the spike shows Graphiti's extraction or retrieval is clearly better, we use it as an ingestion worker and keep Postgres as the system of record. We would only move to FalkorDB/Neo4j if real query patterns demand it.

## 4. Data model (v1)

```
tenant ─┬─ organisation ──< person_org_role >── person ──< identity (type: phone|email|whatsapp, value, verified, confidence)
        │
        ├─ conversation (channel, started_at, status, summary, intent, outcome)
        │     └─< message / turn (speaker, text, ts, media)      ← "episode" = unit of provenance
        │
        ├─ fact (subject_type, subject_id, predicate, object_type, object_id | value_json,
        │        valid_from, valid_to, recorded_at, source_message_id, confidence, status: active|superseded|rejected)
        │
        ├─ commitment (who, what, due_at, status: open|kept|broken|cancelled, source_message_id)
        │     e.g. "promise to pay £480 by 24 Oct", "call back Thursday", "send quote"
        ├─ task, case/matter (groups conversations about the same issue; for law firms = enquiry or matter, with other parties for conflict checks)
        └─ external_record (system: xero|quickbooks|shopify|calendar, type: invoice|payment|order|booking, external_id, payload, synced_at)

edge (tenant_id, src_type, src_id, rel, dst_type, dst_id, valid_from, valid_to, source_message_id)
embedding (tenant_id, owner_type, owner_id, vector)   -- pgvector, for messages, facts, summaries
```

- Every table has a `tenant_id` and is protected by Postgres **RLS**. Tenant comes from the authenticated session, never from LLM output.
- `fact` is bi-temporal. A new contradicting fact **supersedes** the old one (`valid_to` is set). Nothing is overwritten.
- `edge` covers relationships not modelled in typed tables (e.g. person *refers* person, conversation *about* product).

## 5. Pipelines

**Ingestion (async, durable job per message/conversation):**
1. Normalise the channel event into `conversation` + `message`.
2. **Resolve identity** (§6) → person/organisation.
3. **Extract** with an LLM and a fixed JSON schema per tenant: entities, facts, commitments, case linkage, plus the PRD extraction schema (FR-A5). Validate against the schema.
4. **Reconcile**: match against existing facts (same subject + predicate). Supersede, merge or flag the conflict. Low-confidence or sensitive changes (e.g. bank details, address) go to a review queue.
5. Embed messages, facts and summaries. Refresh the **person summary** and **context card**.

**Retrieval (at conversation start and via tools):**
- **Context card** (≤ ~800 tokens), built on identity match: who they are, organisation, last 3–5 interactions on any channel with one-line summaries, open commitments/tasks/cases, key active facts, preferences (channel, times). For voice it is fetched during ringing from the caller ID, using the pre-call hook from FR-P4, and cached in Redis.
- **Tools** for deeper lookups: `get_customer_history(filter)`, `get_open_items()`, `search_facts(query)` (hybrid: pgvector + full-text + 1–2 hop expansion).

## 6. Identity resolution
- **Deterministic first**: exact match on normalised E.164 phone, lower-cased email, WhatsApp ID. Email-domain → organisation match (excluding free-mail domains such as gmail.com).
- **Probabilistic suggestions** (name + organisation + domain + phone similarity) **are never auto-merged**. They go to an owner review queue. For bulk-imported contacts, offline dedup can use [Splink](https://moj-analytical-services.github.io/splink/) (Fellegi-Sunter, unsupervised; built by the UK Ministry of Justice).
- **Merge and split** are reversible and audited. Facts keep their source, so a split re-attributes them correctly.
- **Identity confidence gates disclosure.** Caller ID alone (spoofable) is enough to personalise a greeting. It is *not* enough to reveal account, order or invoice details. Those need verification (e.g. a code sent to a known email/WhatsApp, or matching two known attributes). This is also required for payment conversations (§8).

## 7. Privacy and safety (UK)
- **UK GDPR / DPA 2018**: we are the processor and the business is the controller. Per-person **export** and **erasure** cascade through messages, facts, edges, embeddings and cached cards. Retention per tenant (default: transcripts 12 months, recordings 30 days).
- Inbound text is **untrusted** (prompt-injection). Extraction output never changes identity links or tenant scope directly. High-impact changes need review.
- UK hosting (e.g. AWS eu-west-2 London) for the database and embeddings. LLM vendors under DPA with no training on our data.

## 8. Readiness for payment follow-up (future)
The model above supports chasing outstanding payments without schema changes:
- `external_record` syncs invoices and payments from **Xero** (webhooks for INVOICE create/update, then fetch the resource) or QuickBooks.
- `commitment` records **promises to pay** made on any channel. A workflow engine (durable, scheduled) checks due dates against payments received and triggers a follow-up on the customer's preferred channel and time.
- The graph answers "who is the payer contact at this organisation" and "what was promised, when, and where".
- **Compliance notes:** for the law-firm launch, chasing consumer clients for unpaid fees is consumer debt. CONC 7 most likely applies only if the firm provides regulated credit (to be confirmed with a compliance adviser) (e.g. fee instalment agreements), but its contact rules are sensible defaults anyway. B2B invoice chasing falls under the Late Payment of Commercial Debts (Interest) Act 1998 (statutory interest and compensation). If a tenant chases **consumer credit** debts, FCA **CONC 7** applies: explain who is contacting and why, no contact at unreasonable times, respect requested contact preferences, no disclosure to third parties, and verify identity before discussing the debt ([CONC 7.9](https://www.handbook.fca.org.uk/handbook/CONC/7/9.html)). Build in contact-time windows, per-contact channel preferences, opt-out, and identity verification from the start. Outbound WhatsApp needs approved templates.

## 9. Next steps
1. **Spike (1 week):** take 50 synthetic multi-channel conversations. Compare (a) our extraction prompt into Postgres with (b) Graphiti (Neo4j/FalkorDB driver) on identity linkage, fact accuracy, superseded-fact handling, ingestion cost per message, and retrieval latency. Decision record afterwards.
2. Define `ContextGraph` interface and schema migrations with RLS policies and an erasure test.
3. Context card builder with a latency budget test (≤ 150 ms p95 from cache, ≤ 400 ms cold).
