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
prompt template is `estimator/prompts/intake_agent.txt`; configure its backend tool to
submit exact transcript turns and extracted fields to the same `/voice` endpoint.

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

Inject a vision/OCR implementation of `DocumentParser` for production photos, PDFs,
or bills. It writes through the same evidence application path and cannot bypass
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
