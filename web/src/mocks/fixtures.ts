import type { Schemas } from "../api/client";
import type { AssistantConfig } from "../lib/config";

const ids = {
  tenants: ["10000000-0000-4000-8000-000000000001", "10000000-0000-4000-8000-000000000002"],
  assistants: ["20000000-0000-4000-8000-000000000001", "20000000-0000-4000-8000-000000000002"],
  people: Array.from({ length: 8 }, (_, index) =>
    `30000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  ),
};
const date = (day: number) => `2026-02-${String(day).padStart(2, "0")}T10:30:00Z`;

export const testCallResponse: Schemas["TestCallResponse"] = {
  url: "http://127.0.0.1:7862/?call_token=mock-call-token",
  expires_at: 1792320000,
};

const plumbingConfig: AssistantConfig = {
  business_name: "Brightside Plumbing & Heating",
  language: "English",
  greeting: "Hello, you are speaking with Brightside. How can I help?",
  instructions: "Use clear, friendly language. Ask one question at a time and offer a callback when needed.",
  readback: "critical",
  office_hours: { days: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"], opens: "08:00", closes: "18:00", timezone: "Europe/London" },
  price_list: [
    { service: "Boiler repair visit", price: "From £95", notes: "Parts and follow-up work quoted separately." },
    { service: "Installation survey", price: "No charge", notes: "Survey required before a quote." },
  ],
  policies: ["Do not diagnose dangerous faults remotely; advise callers to leave the area if they smell gas.", "Confirm availability before promising an appointment.", "Only quote listed prices."],
  contacts: { office: "hello@brightside.example", emergency: "0800 000 0000" },
  services: {
    boiler_repair: { label: "Boiler repair", fields: [
      { name: "caller_name", label: "Caller name", type: "string", required: true, critical: true, description: "" },
      { name: "phone", label: "Phone number", type: "phone", required: true, critical: true, description: "" },
      { name: "address", label: "Property address", type: "string", required: true, critical: true, description: "" },
      { name: "postcode", label: "Postcode", type: "string", required: true, critical: false, description: "" },
      { name: "issue", label: "Issue", type: "enum", choices: ["no_heating", "no_hot_water", "leak", "noise", "other"], required: true, critical: false, description: "" },
      { name: "boiler_make", label: "Boiler make", type: "string", required: false, critical: false, description: "" },
      { name: "preferred_visit_date", label: "Preferred visit date", type: "date", required: false, critical: false, description: "" },
      { name: "landlord", label: "Is the caller a landlord?", type: "boolean", required: false, critical: false, description: "" },
    ] },
    installation_quote: { label: "Installation quote", fields: [
      { name: "caller_name", label: "Caller name", type: "string", required: true, critical: true, description: "" },
      { name: "phone", label: "Phone number", type: "phone", required: true, critical: true, description: "" },
      { name: "email", label: "Email address", type: "email", required: false, critical: false, description: "" },
      { name: "property_type", label: "Property type", type: "enum", choices: ["flat", "terraced", "semi_detached", "detached", "other"], required: false, critical: false, description: "" },
      { name: "current_system", label: "Current system", type: "string", required: false, critical: false, description: "" },
      { name: "budget", label: "Budget", type: "string", required: false, critical: false, description: "" },
    ] },
  },
  urgency_rules: [
    { type: "keyword", phrases: ["gas leak", "smell gas", "flooding", "carbon monoxide", "no heating"], reason: "Potentially urgent property issue." },
    { type: "date_deadline", service: "boiler_repair", field: "preferred_visit_date", add_months: 0, minus_days: 0, urgent_within_days: 2, reason: "Requested visit is within two days." },
  ],
  knowledge: { sources: [{ url: "https://brightside.example/" }], faqs: [
    { question: "Do you offer emergency callouts?", answer: "Call the office and the team will confirm current availability." },
    { question: "Do you cover my area?", answer: "Share your postcode so the team can check." },
    { question: "Can you install a new boiler?", answer: "Yes, request a survey for an installation quote." },
  ], max_digest_chars: 24000 },
  live_actions: ["flag_urgent"],
  transfer_targets: [],
  transfer_failed_message: "Sorry, nobody is available to take your call right now.",
  review_confidence_threshold: 0.7,
};

const lawConfig: AssistantConfig = {
  business_name: "Demo & Co Solicitors",
  language: "British English",
  greeting: "Hello, you are speaking with the AI assistant for Demo & Co Solicitors. How can I help?",
  instructions: `Use short, natural spoken sentences. Ask one question at a time.
When the caller volunteers details, acknowledge them briefly and continue. Never ask the same
question twice in a row.

At the beginning of every call, disclose that you are an AI assistant. Say: "{greeting}"

Ask for each critical field at a natural point. If the caller does not answer, acknowledge it,
move on, and return to it once before the end.
Collect other details conversationally; they are extracted after the call. Do not call a tool
for each detail.
Offer to pass unanswered questions to the team.

If the caller describes something urgent, acknowledge it and tell them the team will treat it
as urgent. Do not delay the conversation to finish intake.
Be honest when the business is closed. Office hours: {office_hours}.

When the caller is ready to finish, say goodbye. When the caller says goodbye, say goodbye
in response.`,
  readback: "email",
  office_hours: { days: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], opens: "09:00", closes: "17:30", timezone: "Europe/London" },
  price_list: [
    { service: "Residential conveyancing purchase, freehold", price: "£1,250–£1,800 plus VAT and disbursements", notes: "Illustrative demo range; leasehold, new-build and complex work may cost more." },
    { service: "Residential conveyancing sale", price: "£850–£1,250 plus VAT and disbursements", notes: "Illustrative demo range; actual scope and disbursements affect the total." },
    { service: "Residential remortgage", price: "£595 plus VAT and disbursements", notes: "Fixed fee; disbursements are additional." },
    { service: "Employment settlement agreement review", price: "£500–£900 plus VAT", notes: "Illustrative demo range; confirm scope and any employer contribution." },
    { service: "Employment tribunal initial assessment", price: "£250 plus VAT", notes: "Illustrative demo fee; further work is separately scoped." },
  ],
  policies: [
    "You are not a solicitor and must never give legal advice. Give general information only from approved business information. For advice, arrange a solicitor callback; do not predict legal outcomes.",
    "Never confirm or deny whether anyone is or was a client. Never discuss a matter or disclose personal information to a third party. Caller ID alone does not verify identity. Do not leave detailed voicemails.",
    "Never say the firm will act or that a conflict check has passed. Record other-party names for a conflict check pending.",
    "Answer prices only from the published price list. Never estimate, invent or promise a quote. If no listed price fits, offer a quote request or callback.",
    "If the caller complains, acknowledge it, log it, and give the published complaints contact, procedure and Legal Ombudsman information.",
    "If anyone is in immediate danger, tell them to call 999 now. Treat a hearing or arrest today, distress, or an imminent deadline as urgent; the team is alerted after the call.",
    "For existing-client matter updates, take a message for the fee earner; never imply access to live matter status.",
    "For employment termination dates, the system calculates the tribunal deadline. Do not calculate or reassure callers about legal time limits, and offer a callback.",
  ],
  contacts: {
    complaints_contact: "complaints@demoandco.example",
    complaints_procedure: "Ask for the written complaints procedure from the complaints handler.",
    legal_ombudsman_information: "Legal Ombudsman: www.legalombudsman.org.uk; telephone 0300 555 0333.",
  },
  services: {
    conveyancing: { label: "Residential conveyancing", fields: [
      { name: "caller_name", label: "Caller name", type: "string", required: false, critical: true, description: "" },
      { name: "phone", label: "Phone number", type: "phone", required: false, critical: true, description: "" },
      { name: "email", label: "Email address", type: "email", required: false, critical: true, description: "" },
      { name: "funding", label: "Funding", type: "enum", choices: ["private", "legal_expenses_insurance", "legal_aid", "no_win_no_fee"], required: false, critical: false, description: "" },
      { name: "contact_preference", label: "Contact preference", type: "enum", choices: ["phone", "email"], required: false, critical: false, description: "" },
      { name: "other_parties", label: "Other parties", type: "list", required: false, critical: false, description: "" },
      { name: "transaction_type", label: "Transaction type", type: "enum", choices: ["purchase", "sale", "both", "remortgage"], required: false, critical: false, description: "" },
      { name: "property_address", label: "Property address", type: "string", required: false, critical: false, description: "" },
      { name: "postcode", label: "Postcode", type: "string", required: false, critical: false, description: "" },
      { name: "price", label: "Price", type: "string", required: false, critical: false, description: "" },
      { name: "tenure", label: "Tenure", type: "enum", choices: ["freehold", "leasehold", "shared_ownership"], required: false, critical: false, description: "" },
      { name: "new_build", label: "New build", type: "boolean", required: false, critical: false, description: "" },
      { name: "mortgage", label: "Mortgage", type: "boolean", required: false, critical: false, description: "" },
      { name: "lender", label: "Lender", type: "string", required: false, critical: false, description: "" },
      { name: "first_time_buyer", label: "First-time buyer", type: "boolean", required: false, critical: false, description: "" },
      { name: "chain_status", label: "Chain status", type: "string", required: false, critical: false, description: "" },
      { name: "estate_agent", label: "Estate agent", type: "string", required: false, critical: false, description: "" },
      { name: "target_exchange_date", label: "Target exchange date", type: "date", required: false, critical: false, description: "" },
      { name: "target_completion_date", label: "Target completion date", type: "date", required: false, critical: false, description: "" },
      { name: "other_side_solicitor", label: "Other-side solicitor", type: "string", required: false, critical: false, description: "" },
    ] },
    employment: { label: "Employment law", fields: [
      { name: "caller_name", label: "Caller name", type: "string", required: false, critical: true, description: "" },
      { name: "phone", label: "Phone number", type: "phone", required: false, critical: true, description: "" },
      { name: "email", label: "Email address", type: "email", required: false, critical: true, description: "" },
      { name: "funding", label: "Funding", type: "enum", choices: ["private", "legal_expenses_insurance", "legal_aid", "no_win_no_fee"], required: false, critical: false, description: "" },
      { name: "contact_preference", label: "Contact preference", type: "enum", choices: ["phone", "email"], required: false, critical: false, description: "" },
      { name: "other_parties", label: "Other parties", type: "list", required: false, critical: false, description: "" },
      { name: "issue_type", label: "Issue type", type: "enum", choices: ["dismissal", "redundancy", "discrimination", "settlement_agreement", "grievance"], required: false, critical: false, description: "" },
      { name: "employer_name", label: "Employer name", type: "string", required: false, critical: false, description: "" },
      { name: "job_title", label: "Job title", type: "string", required: false, critical: false, description: "" },
      { name: "length_of_service", label: "Length of service", type: "string", required: false, critical: false, description: "" },
      { name: "termination_date", label: "Termination date", type: "date", required: false, critical: true, description: "" },
      { name: "last_act_complained_of", label: "Last act complained of", type: "date", required: false, critical: true, description: "" },
      { name: "acas_early_conciliation_started", label: "ACAS early conciliation started", type: "boolean", required: false, critical: false, description: "" },
      { name: "key_dates", label: "Key dates", type: "list", required: false, critical: false, description: "Dates and event names mentioned by the caller." },
    ] },
  },
  urgency_rules: [
    { type: "keyword", phrases: ["in danger", "immediate danger", "domestic abuse", "999", "hearing today", "arrested today", "arrest today", "distressed", "deadline today", "deadline within days"], reason: "Caller described an immediate safety or time-critical concern: {phrase}" },
    { type: "date_deadline", service: "employment", field: "termination_date", add_months: 3, minus_days: 1, urgent_within_days: 28, reason: "Employment tribunal time limit may be within 28 days." },
  ],
  knowledge: { sources: [{ url: "https://demoandco.example/" }, { file: "eval/fixtures/demo_docs/client_care.md" }], faqs: [
    { question: "Can I get legal advice by phone?", answer: "The team can arrange a solicitor callback." },
    { question: "How much does a review cost?", answer: "The published price list is illustrative; the team can confirm scope." },
    { question: "Can you check a conflict?", answer: "The team will complete any required checks." },
  ], digest_path: "knowledge/demo_firm/digest.md", max_digest_chars: 24000 },
  live_actions: ["transfer_call"],
  transfer_targets: [
    { key: "duty_solicitor", label: "Duty solicitor", phone: "+447700900123", when_to_use: "The caller explicitly asks to speak to a person now.", office_hours_only: false },
  ],
  transfer_failed_message: "Sorry, nobody is available to take your call right now.",
  review_confidence_threshold: 0.7,
};

const fakeUser: Schemas["UserResponse"] = { id: "40000000-0000-4000-8000-000000000001", email: "demo@example.com", display_name: "Sam Taylor" };
export const memberships: Schemas["TenantResponse"][] = [
  { id: ids.tenants[0], name: "Brightside Plumbing & Heating", role: "owner" },
  { id: ids.tenants[1], name: "Demo & Co Solicitors", role: "staff" },
];
export const authUser = fakeUser;

const tenantCallers = [
  [
    { name: "Jamie Morgan", email: "jamie.morgan@example.com", phone: "+44 7700 900123" },
    { name: "Jamie Morgan", email: "jamie.morgan.alt@example.com", phone: "+44 7700 900123" },
    { name: "Riley Patel", email: "riley.patel@example.com", phone: "+44 7700 900124" },
    { name: "Casey Lee", email: "casey.lee@example.com", phone: "+44 7700 900125" },
  ],
  [
    { name: "Alex Morgan", email: "alex.morgan@example.com", phone: "+44 7700 900231" },
    { name: "Alex Morgan", email: "alex.morgan.alt@example.com", phone: "+44 7700 900231" },
    { name: "Imogen Price", email: "imogen.price@example.com", phone: "+44 7700 900232" },
    { name: "Oliver Grant", email: "oliver.grant@example.com", phone: "+44 7700 900233" },
  ],
] as const;

const conversationSummaries = [
  [
    "Boiler not firing, no hot water since this morning",
    "Quote request for bathroom radiator replacement",
    "Landlord reporting leak under kitchen sink",
    "Annual boiler service availability for a two-bedroom flat",
    "Follow-up on a replacement thermostat installation",
    "Asked whether an engineer can inspect a noisy circulation pump",
  ],
  [
    "Enquiry about unfair dismissal; termination date 3 Sept",
    "Conveyancing quote for flat purchase",
    "Existing client asking for update on probate matter",
    "Request for review of a settlement agreement",
    "Question about leasehold title checks before exchange",
    "Follow-up on a power of attorney appointment",
  ],
] as const;

function makeConversation(tenantIndex: number, index: number): Schemas["ConversationDetailResponse"] {
  const id = `50000000-0000-4000-8000-${String(tenantIndex * 10 + index + 1).padStart(12, "0")}`;
  const review = index === 0 || index === 1;
  const person = ids.people[tenantIndex * 4 + (index % 4)];
  const caller = tenantCallers[tenantIndex][index % 4];
  const summary = conversationSummaries[tenantIndex][index];
  const transferred = tenantIndex === 1 && index === 0;
  const channel = (["phone", "web", "gmail", "whatsapp"] as const)[index % 4];
  const rawName = tenantIndex === 0 ? "boiler_repair.caller_name" : "employment.caller_name";
  const callerName = caller.name;
  const postName = index === 0 ? (tenantIndex === 0 ? "Jamie M." : "Alex M.") : callerName;
  const liveFields = {
    caller_name: { value: callerName, confirmed: true, confirmed_at: date(index + 2) },
    phone: { value: caller.phone, confirmed: false, confirmed_at: null },
    ...(tenantIndex === 0 ? { issue: { value: "no_heating", confirmed: false, confirmed_at: null } } : {}),
  };
  const postFields = {
    caller_name: { value: postName, confidence: review ? 0.51 : 0.96, evidence: "Caller provided their name.", confirmed_by_caller: false },
    phone: { value: caller.phone, confidence: 0.93, evidence: "Caller gave a callback number.", confirmed_by_caller: false },
    ...(tenantIndex === 0 ? { issue: { value: "no_hot_water", confidence: 0.61, evidence: "There is no heating or hot water.", confirmed_by_caller: false } } : {}),
  };
  const mergedCallerName = {
    value: callerName,
    status: review && index === 0 ? "conflict" : "agreed",
    confidence: review ? 0.51 : 0.96,
    needs_review: review,
    confirmed_by_caller: true,
    sources: {
      live: { value: callerName, confirmed_at: date(index + 2) },
      post_call: { value: postName, confidence: review ? 0.51 : 0.96, evidence: "Caller provided their name.", confirmed_by_caller: false },
      caller_id: { value: "+44 7700 900999" },
    },
    ...(index === 0 ? { alternative: postName } : {}),
  };
  const intakes: Schemas["IntakeResponse"][] = [
    { version: "live", data: { service: tenantIndex === 0 ? "boiler_repair" : "employment", fields: liveFields } },
    { version: "post_call", data: { status: index === 5 ? "failed" : "succeeded", service: tenantIndex === 0 ? "boiler_repair" : "employment", model: "fixture-extractor", fields: postFields, callback_requested: true, complaint: false, unanswered_questions: [] } },
    { version: "merged", data: { service: tenantIndex === 0 ? "boiler_repair" : "employment", fields: { caller_name: mergedCallerName, phone: { value: caller.phone, status: "agreed", confidence: 0.93, needs_review: false, confirmed_by_caller: false, sources: { live: { value: caller.phone }, post_call: { value: caller.phone, confidence: 0.93 } } }, ...(tenantIndex === 0 ? { issue: { value: "no_heating", status: "conflict", confidence: 0.61, needs_review: false, confirmed_by_caller: false, sources: { live: { value: "no_heating" }, post_call: { value: "no_hot_water", confidence: 0.61 } }, alternative: "no_hot_water" } } : {}) }, needs_review: review ? [rawName] : [] } },
  ];
  const task: Schemas["TaskSummary"] = { id: `60000000-0000-4000-8000-${String(tenantIndex * 10 + index + 1).padStart(12, "0")}`, kind: ["callback", "urgent", "quote", "knowledge_gap"][index % 4], status: index % 3 === 0 ? "done" : "open", due_at: date(index + 3), source: index % 2 === 0 ? "live" : "post_call", payload: { reason: "Follow up with the caller", summary: "Confirm next steps" } };
  task.conversation_id = id;
  task.person_id = person;
  const tasks = [task];
  if (transferred) {
    tasks.push({
      ...task,
      id: `${id}-transfer-failed`,
      kind: "transfer_failed",
      payload: { target_key: "duty_solicitor", dial_status: "no-answer", reason: "Caller asked for a person." },
      source: "live",
    });
  }
  return {
    id, assistant_id: ids.assistants[tenantIndex], person_id: person, channel,
    external_ref: channel === "phone" || channel === "whatsapp" ? caller.phone : caller.email,
    contact_name: caller.name,
    needs_review: review,
    test_call: tenantIndex === 0 && index === 0,
    status: (["completed", "completed", "active", "failed", "completed", "completed"] as const)[index],
    started_at: date(index + 1), ended_at: index === 2 ? null : date(index + 1), summary,
    end_reason: transferred ? "transferred" : index === 3 ? "connection_lost" : "caller_hangup",
    transfer: transferred ? { target_key: "duty_solicitor", status: "no-answer", latency_ms: 1200 } : null,
    messages: [
      { id: `${id}-m1`, role: "caller", text: summary, ts: date(index + 1), meta: {} },
      { id: `${id}-m2`, role: "assistant", text: "Of course. Can I take your name and a callback number?", ts: date(index + 1), meta: {} },
      { id: `${id}-m3`, role: "caller", text: `${callerName}, and the best number is ${caller.phone}.`, ts: date(index + 1), meta: {} },
    ],
    intakes, tasks,
    metrics: { call_duration_seconds: 185 + index * 10, providers: { stt: "mock-stt", tts: "mock-tts" }, turn_latency_summary: { p50_seconds: 0.42, p95_seconds: 0.81, max_seconds: 1.02 }, turn_end_latency_summary: { p50_seconds: 0.19, p95_seconds: 0.31, max_seconds: 0.44 }, usage: { stt_seconds: 180, tts_characters: 490 }, ...(transferred ? { transfer: { target_key: "duty_solicitor", status: "no-answer", latency_ms: 1200 } } : {}) },
    estimated_cost: { currency: "GBP", components: { stt: 0.01, tts: 0.02, llm_input: 0.01, llm_output: 0.02, twilio: 0.04, extraction: 0.005 }, total: 0.105, unknown_components: [] },
  };
}

function makeContacts(tenantIndex: number): Schemas["ContactDetailResponse"][] {
  return Array.from({ length: 4 }, (_, index) => {
    const personId = ids.people[tenantIndex * 4 + index];
    const caller = tenantCallers[tenantIndex][index];
    const displayName = index === 1 ? `${caller.name} (possible duplicate)` : caller.name;
    const conversation = makeConversation(tenantIndex, index);
    return {
      id: personId, display_name: displayName, merged_into_id: null, created_at: date(index + 1), updated_at: date(index + 4),
      identities: [
        { id: `${personId}-identity-1`, type: "phone", value_normalized: caller.phone.replaceAll(" ", ""), verified: index === 0, confidence: 0.94, source: "caller_id" },
        { id: `${personId}-identity-2`, type: "email", value_normalized: caller.email, verified: false, confidence: 0.82, source: "conversation" },
      ],
      active_facts: [{ id: `${personId}-fact-1`, predicate: "preferred_contact_method", value: "phone", valid_from: date(index + 1), valid_to: null, evidence: "Caller requested a callback.", confidence: 0.9, status: "active" }],
      conversations: [conversation],
      open_tasks: conversation.tasks.filter((task) => task.status === "open"),
      open_commitments: [{ id: `${personId}-commitment`, what: "Team to call back", due_at: date(index + 6), status: "open" }],
      context_card: `${displayName} called about a service question. A callback was requested. Previous contact was on ${date(index + 1)}.`,
    };
  });
}

export interface TenantFixture {
  tenant: Schemas["TenantResponse"];
  assistants: Schemas["AssistantResponse"][];
  assistantData: Record<string, {
    versions: Schemas["AssistantVersionResponse"][];
    sources: Schemas["KnowledgeSourceResponse"][];
    digest: Schemas["KnowledgeDigestResponse"];
  }>;
  conversations: Schemas["ConversationDetailResponse"][];
  contacts: Schemas["ContactDetailResponse"][];
  gaps: Schemas["KnowledgeGapResponse"][];
  tasks: Schemas["TaskSummary"][];
}

function makeTenant(index: number): TenantFixture {
  const tenant = memberships[index];
  const config = index === 0 ? plumbingConfig : lawConfig;
  const assistant: Schemas["AssistantResponse"] = {
    id: ids.assistants[index], name: index === 0 ? "Brightside reception" : "Demo & Co reception",
    template: index === 0 ? "home_services" : "uk_law_firm", config, version: 3, created_at: date(1), updated_at: date(10),
  };
  const conversations = Array.from({ length: 6 }, (_, item) => makeConversation(index, item));
  const contacts = makeContacts(index);
  const taskList = conversations.flatMap(({ tasks }) => tasks);
  return {
    tenant, assistants: [assistant],
    assistantData: {
      [assistant.id]: {
        versions: [1, 2, 3].map((version) => ({
          id: `${assistant.id}-version-${version}`, version, created_by: fakeUser.id,
          config: version === 3 ? config : { ...config, greeting: version === 2 ? `${config.greeting} Welcome.` : config.greeting, review_confidence_threshold: version === 1 ? 0.6 : config.review_confidence_threshold },
          created_at: date(version + 2),
        })),
        sources: [
          { id: `${assistant.id}-source-1`, assistant_id: assistant.id, kind: "url", uri: index === 0 ? "https://brightside.example/" : "https://demoandco.example/", status: "succeeded", manifest: {}, fetched_at: date(5), created_at: date(1) },
          { id: `${assistant.id}-source-2`, assistant_id: assistant.id, kind: "file", uri: "service-guide.pdf", status: "pending", manifest: {}, fetched_at: null, created_at: date(6) },
        ],
        digest: { id: `${assistant.id}-digest`, assistant_id: assistant.id, content: index === 0 ? "Brightside provides plumbing and heating support. The team confirms availability and pricing." : "Demo & Co provides general information and arranges solicitor callbacks.", edited_by: fakeUser.id, created_at: date(5) },
      },
    },
    conversations, contacts,
    gaps: [0, 1, 2].map((n) => ({
      id: `${assistant.id}-gap-${n + 1}`, assistant_id: assistant.id, conversation_id: conversations[n].id,
      question: ["Do you cover this postcode?", "What is the cost for this service?", "Can I book for next week?"][n],
      evidence: "The caller asked for information that was not available in the current knowledge sources.",
      status: (n === 1 ? "answered" : "open") as Schemas["KnowledgeGapResponse"]["status"], created_at: date(n + 1),
    })),
    tasks: taskList,
  };
}

export const tenantFixtures: TenantFixture[] = [makeTenant(0), makeTenant(1)];
export const templates: Schemas["TemplateResponse"][] = [
  { name: "home_services", config: plumbingConfig },
  { name: "uk_law_firm", config: lawConfig },
  { name: "blank", config: { ...plumbingConfig, business_name: "Your business", greeting: "Hello, how can I help?", instructions: "", services: {}, urgency_rules: [], policies: [], price_list: [], contacts: {}, knowledge: { faqs: [], max_digest_chars: 24000 }, live_actions: [], readback: "off" } },
];

export function cloneFixtures(): TenantFixture[] {
  return JSON.parse(JSON.stringify(tenantFixtures)) as TenantFixture[];
}
