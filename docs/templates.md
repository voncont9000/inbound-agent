# Assistant templates

Assistant templates are configuration presets for new assistants. The YAML files
live in `voice/config/templates/`; the backend loads them through
`inbound_core.templates`, and `/templates` publishes their metadata and resolved
assistant configuration.

New assistants start from **Blank** by default. Blank includes generic
conversation behavior, one general-enquiry service, and generic email triage,
but no industry-specific policies, rules, urgency rules, pronunciations,
technical terms, or live actions. Industry content comes from the template the
user selects. Existing assistants keep their stored full configuration; adding
or changing a template does not change saved assistants.

## Metadata and sample content

Each YAML file has a top-level `template:` block alongside the regular assistant
configuration:

```yaml
template:
  label: Example industry
  description: A short description for the template picker.
  order: 60
  sets_up:
    - Service-specific intake
    - Industry policies and urgency rules
  sample:
    business_name: Example Business (sample)
    company_info: |
      SAMPLE CONTENT - replace with your business's details.
      Example facts for a demo assistant.
    faqs:
      - question: Example question?
        answer: Example answer.
    price_list:
      - service: Example service
        price: "Sample price"
        notes: "Sample price details."
```

The metadata block is removed before the assistant configuration is validated.
Template names come from the YAML filename and use lowercase letters, digits,
and underscores. Templates are listed by `order`, then name. The Blank template
has order `0`; industry templates follow it.

The optional `sample` content is **not** applied when a user simply selects a
template or creates an assistant with its defaults. It is applied only by the
**Create demo assistant** button, which calls
`POST /assistant-setup/complete` with `use_sample_content: true`. The sample
company details become a knowledge digest; sample FAQs and prices are applied
as setup-draft sections. Explicit non-null `accepted` sections override their
corresponding sample sections. Demo details must be replaced before an
assistant goes live.

## Adding a template

1. Add a file such as `voice/config/templates/example_industry.yaml` with a
   complete `AssistantConfig` and a valid `template:` metadata block.
2. Give it a unique lowercase-underscore filename, a display label and
   description, a sort order, and concise `sets_up` bullet points. Add a
   `sample` only if a useful demo can be provided; mark its business details as
   sample content.
3. Keep business-specific policies, intake fields, urgency rules, greetings,
   prompts, email triage and technical terms in the YAML rather than code
   defaults.
4. Run the core template validation test:

   ```sh
   cd core && uv run pytest tests/test_templates.py tests/test_config.py
   ```

   It loads every YAML template, checks metadata and configuration invariants,
   verifies deadline-rule references, and renders each template's business
   context. `test_config.py` also verifies that an assistant extending a
   metadata-bearing template loads successfully.
5. Regenerate API and mock fixtures after changing templates:

   ```sh
   uv run --directory backend python -m inbound_api.export_templates
   uv run --directory backend python -m inbound_api.export_openapi
   cd web && npm run api:types
   ```

## Template library

### Blank

- **File:** `voice/config/templates/blank.yaml`
- **Sets up:** AI disclosure at call start; one general enquiry with name,
  phone, email and reason; generic email triage.
- **Services and critical fields:** General enquiry — caller name and phone
  number.
- **Urgency rules:** None.
- **Sample business:** None.

### UK B2C law firm

- **File:** `voice/config/templates/uk_law_firm.yaml`
- **Sets up:** Conveyancing and employment intake, conflict-check and
  confidentiality policies, no legal advice, urgent safety/hearing/arrest
  handling, tribunal deadline handling, and legal-enquiry email triage.
- **Services and critical fields:** Residential conveyancing — caller name,
  phone and email; employment law — caller name, phone, email, termination date
  and last act complained of.
- **Urgency rules:** Keyword escalation for danger, domestic abuse, hearings or
  arrests today, distress and imminent deadlines; an employment
  `termination_date` deadline rule that flags qualifying tribunal deadlines
  within 28 days.
- **Sample business:** Demo & Co Solicitors (sample law firm).

### Restaurant

- **File:** `voice/config/templates/restaurant.yaml`
- **Sets up:** Table reservation, large-group/event and general enquiry intake;
  allergy and dietary safety; emergency handling; booking/event email triage.
- **Services and critical fields:** Table reservation — caller name, phone,
  booking date, booking time and party size; large group or event — caller name,
  phone, email, event date and party size; general enquiry — caller name and
  phone.
- **Urgency rules:** Keyword escalation for allergic reactions and
  food-safety/medical incidents; sentence rules for same-day bookings and
  serious food-safety complaints.
- **Sample business:** The Copper Pot (sample restaurant).

### Dental or medical clinic

- **File:** `voice/config/templates/dental_clinic.yaml`
- **Sets up:** New-patient, appointment-change and emergency intake; no medical
  advice or diagnosis; patient confidentiality; dental and medical emergency
  handling; patient-query email triage.
- **Services and critical fields:** Appointment request and change/cancel —
  patient name, phone and date of birth; pain or emergency — patient name,
  phone and symptoms described.
- **Urgency rules:** Keyword escalation for breathing or swallowing difficulty,
  swelling, uncontrolled bleeding, serious injury and severe pain; a sentence
  rule for severe pain, injury, swelling or bleeding.
- **Sample business:** Riverside Dental Practice (sample clinic).

### Home services and trades

- **File:** `voice/config/templates/home_services.yaml`
- **Sets up:** Emergency callout, quote request and general enquiry intake
  with address and postcode; gas and electrical safety; job/quote email triage.
- **Services and critical fields:** Emergency callout — caller name, phone,
  address, postcode and problem; quote or booked job — caller name, phone,
  address and postcode; general enquiry — caller name and phone.
- **Urgency rules:** Keyword escalation for gas, carbon monoxide, flooding,
  leaks, heating or hot-water loss, sparking and burning smells; a sentence
  rule for vulnerable people without heating or water.
- **Sample business:** Brightside Plumbing & Heating (sample trades business).

### Real estate and lettings

- **File:** `voice/config/templates/lettings_agency.yaml`
- **Sets up:** Viewing, maintenance, valuation/landlord and general enquiry
  intake; no promises about availability, offers or deposits; tenant and
  landlord confidentiality; emergency repair handling; property-enquiry email
  triage.
- **Services and critical fields:** Viewing request — caller name, phone, email
  and property reference; maintenance report — tenant name, phone, property
  address, postcode and issue; valuation/landlord enquiry — caller name, phone
  and property address; general enquiry — caller name and phone.
- **Urgency rules:** Keyword escalation for gas, carbon monoxide, flooding,
  leaks, heating or hot-water loss, break-ins, lockouts and sparking; a sentence
  rule for repairs that make a property unsafe, insecure or uninhabitable.
- **Sample business:** Harbour Lettings & Sales (sample agency).
