# Nego Caller

The Caller is an isolated FastAPI service for one vendor conversation against one
immutable, confirmed job specification. It incrementally stores quote claims with
vendor transcript evidence and finalizes exactly one structured conversation outcome.

## What is implemented

- `POST /api/v1/calls` creates a call only when the input gateway returns a confirmed
  specification and a known vendor.
- `POST /api/v1/calls/{call_id}/start` re-hashes the specification, creates a voice
  session, and sends an immutable call context.
- `POST /api/v1/calls/{call_id}/events` records transcript/provider events and moves
  the backend-owned state machine.
- `POST /api/v1/calls/{call_id}/finalize` validates and stores one of
  `complete_quote`, `callback_required`, `declined`, or `incomplete_quote`.
- `GET /api/v1/calls/{call_id}` returns the call, transcript, original quote,
  structured outcome, and recording reference.
- SQLite owns calls, events, transcript events, quote versions, itemized costs,
  terms, evidence links, and outcomes.
- Provider tools log each quote fact during the conversation. Vendor transcript
  evidence is mandatory and cross-call evidence is rejected.
- Both sides of every conversation are durably stored in sequence and supplied to
  GPT as bounded conversation history, alongside the cumulative structured quote.
- Competing-bid leverage must be supplied as a verified, evidence-referenced policy
  input for the same immutable job. The Caller cannot select or invent leverage.
- A deterministic simulated voice adapter supports end-to-end development before
  ElevenLabs, Twilio, or SIP is connected.
- The local demo supports microphone audio -> ElevenLabs Scribe v2 -> GPT-5.4 ->
  ElevenLabs Flash v2.5 -> browser audio playback.

The challenge-level product constraints are recorded in
[`docs/PRODUCT_CONTEXT.md`](docs/PRODUCT_CONTEXT.md).

The module does **not** edit job specifications, generate prices, decide concessions,
rank vendors, produce reports, or accept contracts.

## Layout

```text
apps/api/app/caller/
├── router.py                 # HTTP boundary
├── schemas.py                # Input/output contracts
├── orchestrator.py           # Application service and immutable-spec checks
├── state_machine.py          # Backend-owned lifecycle
├── outcome_validator.py      # Exactly-four-outcome validation
├── quote_draft.py            # Incremental quote writes
├── evidence.py               # Transcript evidence enforcement
├── persistence.py            # Caller-owned SQLite tables
├── input_gateway.py          # Read-only upstream integration
├── prompts/buyer_agent.txt   # Conversational behavior only
├── tools/                    # Voice-agent backend tools
└── adapters/                 # Simulated and ElevenLabs provider seams
```

## Run locally

Python 3.11 or newer is required.

```powershell
python -m pip install -e ".[dev]"
uvicorn apps.api.app.main:app --reload
```

Run tests:

```powershell
python -m pytest -q
```

## Try the voice pipeline

Configure both providers in the same PowerShell window that will run the API:

```powershell
$env:OPENAI_API_KEY = "your-openai-key"
$env:ELEVENLABS_API_KEY = "your-elevenlabs-key"
$env:ELEVENLABS_VOICE_ID = "your-elevenlabs-voice-id"
```

The keys stay server-side and are never sent to the browser. Optional model overrides
default to `gpt-5.4`, `scribe_v2`, and `eleven_flash_v2_5`:

```powershell
$env:OPENAI_MODEL = "gpt-5.4"
$env:ELEVENLABS_STT_MODEL = "scribe_v2"
$env:ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"
```

Start the local API:

```powershell
uvicorn apps.api.app.main:app --reload
```

Then open `http://127.0.0.1:8000/demo/` in a modern browser and select **Start
test call**. Allow microphone access, select **Record vendor reply**, speak, and stop
the recording. The backend sends the recording to ElevenLabs for transcription,
runs the resulting text through the Caller and GPT, then sends the buyer response to
ElevenLabs for speech generation. The browser receives only the generated audio.

Typing remains available for quick testing; typed turns still use GPT and ElevenLabs
voice output, but skip speech-to-text.

The page includes a four-message quick test script that produces a complete quote.
You can also try phrases such as “I will call you back tomorrow” or “I decline to
quote” to exercise the other outcomes. When `OPENAI_API_KEY` is present, the demo uses
`gpt-5.4` through the Responses API for natural conversation and same-turn structured
fact extraction. Without a key, it falls back to the deterministic simulator. Both
paths write through the same transcript, evidence, quote, state-machine, and
finalization code. If ElevenLabs configuration is missing, the voice endpoint returns
a clear configuration error rather than silently switching to browser speech.

Enter an optional **Verified binding competing bid** before starting the demo to test
an honest price-match turn. In production, the comparison/negotiation layer supplies
the same data through `CallPolicy`:

```json
{
  "verified_competing_bids": [{
    "bid_id": "bid_1850",
    "source_call_id": "call_previous_vendor",
    "job_spec_version_id": "spec_123",
    "total": 1850,
    "currency": "USD",
    "binding_status": "binding",
    "evidence_reference": "transcript://call_previous_vendor/te_481"
  }],
  "approved_leverage_bid_id": "bid_1850"
}
```

Only the externally approved bid can be stated. Its job-spec version must match the
current call, and its source/evidence reference remains attached to the call policy.

Override the model only when intentionally testing another compatible model:

```powershell
$env:OPENAI_MODEL = "gpt-5.4"
```

Run a credential/model smoke test without launching the web UI:

```powershell
python -m scripts.smoke_openai
```

The normal `/api/v1/calls` endpoints use an empty `InMemoryCallerInputGateway` by
default, so production-style call creation correctly fails until an upstream input
source is injected. The separate `/api/v1/demo` harness owns only its fixed local test
fixtures. This prevents the Caller from quietly becoming an intake or vendor-management
module.

## Connect upstream data

Implement `CallerInputGateway`, or inject `SQLiteCallerInputGateway` when the shared
database already contains upstream-owned `job_spec_versions` and `vendor_targets`
tables. The SQLite gateway only reads these shapes:

```sql
-- owned by Intake
job_spec_versions(version_id, status, facts_json, confirmed_at)

-- owned by vendor targeting
vendor_targets(vendor_id, name, phone, metadata_json)
```

Composition stays at the application boundary:

```python
store = SQLiteCallerStore("nego.db")
inputs = SQLiteCallerInputGateway(store.connection)
orchestrator = CallOrchestrator(store, inputs, SimulatedVoiceSessionAdapter())
```

No Caller method writes those upstream tables. On call creation it stores a canonical
SHA-256 of the confirmed spec; the hash is verified again before session start, every
agent event, and every quote write.

## Provider integration

`ElevenLabsAudioAdapter` owns the demo's Scribe STT and Text-to-Speech HTTP calls.
`SimulatedVoiceSessionAdapter` still owns the provider-neutral call-session seam used
by the Caller orchestrator. Future realtime ElevenLabs Agents, Twilio, or SIP sessions
can implement that seam while reusing the same state machine and quote tools. Provider
callbacks should map to `AgentEvent`; provider tool calls should map to the classes in
`caller/tools`.

The event lifecycle is backend-controlled:

```text
created -> connecting -> disclosure -> job_presentation -> quote_collection
        -> quote_clarification -> summary_confirmation -> terminal outcome
```

Technical connection failures terminate as `failed`; they are distinct from the four
valid outcomes of a conversation that actually connected.

## Evidence rule

A quote write needs an existing transcript event whose speaker is `vendor`, belongs
to the same call, and has a matching timestamp. For example:

```json
{
  "category": "labor",
  "description": "Two-person moving crew",
  "amount": 150,
  "evidence": {
    "transcript_event_id": "te_481",
    "timestamp_seconds": 93.4,
    "confidence": "explicit",
    "source": "vendor_statement"
  }
}
```

Post-call extraction can be added as a repair path, but should use the same evidence
validator and must never overwrite the original evidence-backed quote version.
