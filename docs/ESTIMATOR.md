# Estimator architecture

The Estimator produces one user-confirmed, immutable job specification for reuse by
the Caller. It does not call vendors, store benchmark prices, rank vendors, negotiate,
or mutate a confirmed version.

## Evidence invariant

Every selected field is an `EvidencedField` containing a value (or the literal
`unknown`), source modality, concrete source reference, capture time, and confidence.
Catalog values additionally contain the full candidate set and a selection whose
`selected_by` value can only be `user_confirmation`.

Voice values are rejected when the submitted value does not occur in the referenced
turn. Document evidence carries document ID, filename, document type, and region.
Low-confidence document results remain unselected until review. Conflicting evidence
is retained rather than overwritten and must be resolved by an evidence selection.

## State and storage

```text
draft -> resolving -> awaiting_confirmation -> confirmed
                                                |
                                                +-- edit -> new draft/version
```

Estimator-owned tables contain sessions, voice turns, field evidence candidates,
question attempts, catalog resolutions, and confirmed-version metadata. The shared
`job_spec_versions` table is written only inside the confirmation transaction.

An old version remains immutable and readable through the Caller gateway after an edit.
Estimator metadata records `superseded_by`; the shared row remains confirmed so calls
already bound to that version can continue verifying the original hash.

## Voice interview

The deterministic intake planner selects the highest-priority missing required field.
It asks twice at most before explicitly offering to record `unknown`; unknown is not
closed until the user acknowledges it. GPT/ElevenLabs may phrase the selected question,
but cannot choose a field, apply a default, select a catalog candidate, or confirm.

When `ELEVENLABS_INTAKE_AGENT_ID` and `ELEVENLABS_API_KEY` are configured, starting the
voice endpoint obtains an authenticated ElevenLabs Agents signed WebSocket URL. The
Estimator Lab mounts the official ElevenLabs Agents widget with that short-lived URL;
the API key remains server-side. The prompt template is
`estimator/prompts/intake_agent.txt`.

Configure one blocking ElevenLabs **client tool** named
`capture_intake_evidence` with these parameters:

```text
user_text: string (required)
field_name: string (required)
value: string (required unless mark_unknown is true)
mark_unknown: boolean
unknown_acknowledged: boolean
```

The browser registers that tool before the conversation starts. Each tool call posts
the transcript-backed value to the same `/voice` application service used in tests,
then returns the planner's next field and spoken question to the agent. Configure the
tool to wait for its response so the agent cannot race ahead of durable evidence.

### Post-call transcript fallback

If the browser tool fails, `POST
/api/v1/intake/sessions/{session_id}/elevenlabs-import` accepts a completed ElevenLabs
`conversation_id`. The server retrieves the conversation with the workspace API key
and rejects it unless its Agent ID and initiation-time `intake_session_id` match the
draft. GPT then proposes schema fields using user turns only. Every accepted value must
be an exact substring of its referenced turn or the backend drops it. Imported fields
remain a draft and follow the normal evidence review and explicit-confirmation path.

The extraction model is configured by `OPENAI_ESTIMATOR_MODEL`, falling back to
`OPENAI_RESEARCH_MODEL` and then `gpt-5.6-luna`. This repair path does not infer
unknowns, use agent questions as evidence, or write directly to `job_spec_versions`.

## Document parser seam

The built-in deterministic parser accepts `moving_inventory_json` and
`existing_quote_json` fixtures shaped as:

```json
{
  "fields": [{
    "field_name": "destination.access",
    "value": "second floor with one flight",
    "region": {"page": 1, "line": 8, "bbox": [0, 70, 400, 90]},
    "confidence_score": 0.96
  }]
}
```

The Estimator Lab exposes `moving_inventory_json` as its document route. Inject a
vision/OCR implementation of `DocumentParser` for production photos, PDFs, or bills.
Every parser writes through the same draft evidence store as voice and cannot bypass
confirmation.

## Catalog and benchmark boundary

`CatalogResolver.search` returns candidates only. `/resolve` requires the user to
select an offered candidate and stores the full disambiguation chain. Zero results
leave the vague field unchanged. Results are capped at five in the intake surface.

`BenchmarkRef` stores only field, source, key, and attachment time. Extra properties
such as `price` are schema errors. The benchmark store remains a Closer/ranking concern.

## Confirmation handoff

Confirmation requires `approved: true`, a confirming user identity, every required
field either evidenced or acknowledged unknown, no unresolved conflicts, and no
pending catalog choice. It runs an evidence audit, creates a new version ID, calculates
the same canonical SHA-256 payload used by the Caller, and atomically writes the shared
confirmed row.

The local Estimator Lab passes that confirmed `version_id` to Caller Lab in the page
URL. Caller Lab creates its call through the same Estimator-aware input gateway used by
the API, re-verifies the hash, and gives the flattened confirmed field values to the
dialogue planner and GPT. The built-in demo specification remains only a fallback when
no Estimator version is selected.

Caller Lab separately reads `GET /api/v1/intake/specs/{version_id}` to render the
Estimator evidence before a call starts. This preview preserves the complete
`EvidencedField` rather than the flattened speaking value, so the interface can show
source modality, confidence, and the voice-turn, document-region, or catalog-selection
reference without coupling those provenance details to Caller strategy.

Voice and document provenance necessarily differ, but both modalities produce the
same vertical schema and field/value contract. The Caller receives the exact stored
`facts_json` for one confirmed `version_id`; every call bound to that version verifies
the same canonical hash before starting.

## GPT research context

After the voice interview (or document path) closes the required-field set, the lab
calls `POST /api/v1/research/intake/sessions/{session_id}/enrich`. The research adapter
receives the exact transcript, selected evidence, unresolved fields, vertical, and
schema version. It uses the OpenAI Responses API with web search and returns a typed
bundle containing a topic summary, terminology, risks, assumptions to verify, open
vendor questions, source-linked claims, sources, and limitations.

This bundle is deliberately outside `job_spec_versions.facts_json`. The confirmed spec
therefore remains the customer-fact contract and keeps its canonical hash. Research is
an advisory input only. The API stores the model and provider response ID alongside the
bundle so the context used for a call can be audited.

When a call starts, `CallerResearchContextProvider` persists a snapshot containing the
latest Estimator bundle and earlier terminal calls for that same version. Each prior
call includes its transcript, structured terms, itemized lines, outcome, warnings, and
recording reference. The Caller prompt may use these records to improve its questions,
but may disclose a competing price only through the separate verified-leverage input.

After the selected call set is terminal, `POST /api/v1/reports/{version_id}/prepare`
runs a fresh final research pass. The returned report context keeps external research
separate from stored quote evidence; the reporting prompt is explicitly prohibited
from using research to fill a missing fee, total, deadline, or vendor statement.
