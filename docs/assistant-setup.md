# Website-based assistant setup

Website-based setup creates a new assistant from a template and optionally drafts
configuration sections from a public business website. The user reviews each
draft section before it can be applied. Setup without a website creates an
assistant with the selected template defaults.

## Flow and endpoints

The web flow asks for the business name, an optional assistant name, an optional
website URL, and a template. When a website is supplied, the app submits a draft
request and polls the resulting job. The user can accept, edit and accept, or
discard each drafted section. Sections left pending are not sent during
completion. If drafting fails, the user can continue with template defaults.

`POST /assistant-setup/drafts` is owner-only and returns `202` with a job. It
requires a business name, website URL and template. The worker stores the draft
result in `jobs.result`; it does not create or modify assistants, knowledge
digests, or knowledge sources.

The successful job result has this shape:

```json
{
  "draft": {
    "company_info": "…",
    "faqs": [{"question": "…", "answer": "…"}],
    "office_hours": {
      "days": ["Monday"],
      "opens": "09:00",
      "closes": "17:00",
      "timezone": "Europe/London"
    },
    "price_list": [{"service": "…", "price": "…", "notes": "…"}],
    "services": {"service_key": {"label": "…", "fields": []}}
  },
  "warnings": [],
  "pages": [{"url": "https://example.com/", "title": "Example"}],
  "model": "gemini-3.1-flash-lite",
  "usage": {"input_tokens": 1200, "output_tokens": 300},
  "cost_usd": 0.00075
}
```

Unavailable sections are `null`. `warnings` reports parsing issues,
`pages` contains crawl metadata without scraped page text, and `cost_usd` is
`null` if the selected model has no price entry.

`POST /assistant-setup/complete` is owner-only and returns the created
assistant (`201`). It accepts `name`, `business_name`, `template`, optional
`website_url`, and `accepted`, which is a `SetupDraft` containing only sections
the user chose to apply. The accepted data is applied to a deep copy of the
template. Accepted FAQs are merged with template FAQs, with accepted entries
winning question duplicates. The assistant is created through the standard
assistant creation path, including its initial configuration version.

Accepted `company_info` is stored as the assistant's knowledge digest, not in
the assistant configuration. If a website URL is provided at completion, it is
saved as a pending knowledge source and a knowledge import is queued
automatically. The crawl runs in a worker after setup is complete.

## Safety and acceptance

The API accepts only HTTP(S) URLs with a hostname. Unless
`knowledge_allow_private_urls` is enabled, URL and resolved-address checks use
the knowledge-import public-address allowlist; the crawl runs in a worker
thread and pins address checks for crawl requests. Redirect and address
validation prevent a public starting URL from being used to reach private
networks.

Website text is untrusted input, not instructions. The model prompt directs the
model to use only website facts and not invent answers, services, hours, or
prices. Each returned section is validated independently; invalid sections are
omitted with warnings while other valid sections remain reviewable.

Drafts are kept in the job result and are not inserted into a live assistant
configuration. Completion receives only explicitly accepted sections. The
setup completion audit records the accepted section names.

## Model, cost, and settings

The default model is `gemini-3.1-flash-lite`. Cost is calculated from reported
token usage and the configured per-million-token rates:

```text
cost_usd = input_tokens × input_rate_per_1m / 1,000,000
         + output_tokens × output_rate_per_1m / 1,000,000
```

The current price table lists `$0.25` per million input tokens and `$1.50` per
million output tokens for this model. Cost is an estimate based on reported
usage; no additional website-crawl fee is calculated here.

Settings:

- `SETUP_DRAFT_MODEL` selects the model; default: `gemini-3.1-flash-lite`.
- `SETUP_DRAFT_MAX_PAGES` limits pages for setup; default: `10`. The effective
  page cap is also bounded by `KNOWLEDGE_MAX_PAGES`.
- `GOOGLE_API_KEY` is required to request a draft.
- `KNOWLEDGE_ALLOW_PRIVATE_URLS` controls whether the knowledge-import URL
  safety checks permit private addresses.
- `KNOWLEDGE_REQUEST_DELAY` controls crawl pacing.
