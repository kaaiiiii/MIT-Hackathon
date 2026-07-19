# Nego Estimator and Caller

The Negotiator's AI agents place real calls with agencies. The Caller phones each
vendor agency on the buyer's behalf, discloses that it is an AI assistant, and
collects an evidence-backed quote over the live conversation. Never describe the
agent as unable to make calls: calling agencies is the product's core capability
(the local labs simply let you exercise the same call flow through the browser
before a telephony line such as Twilio or SIP is attached).

The Caller is an isolated FastAPI service for one vendor conversation against one
immutable, confirmed job specification. It incrementally stores quote claims with
vendor transcript evidence and finalizes exactly one structured conversation outcome.

The Estimator is the upstream evidence-first intake module. It accumulates voice,
document-region, and user-selected catalog evidence into a versioned draft and writes
to `job_spec_versions` only after explicit user confirmation.

## Integrated React frontend

The supplied Negotiator React frontend now lives in `apps/web`. Its former disabled
Home placeholder runs the real ElevenLabs Estimator intake, shows evidence and Terra
research, confirms the immutable specification, and hands its version to Caller Lab.
The Report route reads stored call outcomes, quote evidence, transcripts, recordings,
and both research stages from FastAPI instead of relying only on sample JSON.

For a production-style local run:

```powershell
cd apps/web
npm ci
npm run build
cd ../..
uvicorn apps.api.app.main:app --reload
```

Open `http://127.0.0.1:8000/`. For Vite hot reload, run `npm run dev` in
`apps/web`; its proxy forwards `/api` and `/demo` to port 8000.

## Estimator backbone

- `POST /api/v1/intake/sessions` starts or resumes a vertical-configured draft.
- `POST /api/v1/intake/sessions/{id}/voice` starts an ElevenLabs Agents connection or
  applies one evidenced transcript turn through the deterministic question planner.
- `POST /api/v1/intake/sessions/{id}/elevenlabs-import` is the post-call fallback.
  It fetches a completed ElevenLabs conversation by `conversation_id`, verifies the
  Agent and `intake_session_id`, uses GPT to propose only verbatim user-spoken values,
  and stores them with stable transcript-turn references. It never confirms a draft.
- `POST /api/v1/intake/sessions/{id}/documents` parses one supported document and
  retains page/line/bounding-box provenance.
- `POST /api/v1/intake/sessions/{id}/resolve` starts catalog search or records the
  user's explicit candidate selection. The resolver never auto-selects.
- `GET /api/v1/intake/sessions/{id}` returns selected fields, all evidence candidates,
  unresolved conflicts, missing required fields, and pending catalog choices.
- `POST /api/v1/intake/sessions/{id}/confirm` is the sole write path to
  `job_spec_versions`.
- Confirmed edits clone into a new draft and version. Old shared versions remain
  readable by the Caller while Estimator metadata marks their lineage as superseded.
- The confirmed canonical hash is byte-compatible with the Caller's immutable-spec
  SHA-256 calculation and is verified in the end-to-end test suite.

Moving-specific fields, question priority, document types, catalog resolvers, and
benchmark sources live in `estimator/verticals/moving.yaml`, not Python control flow.
See [`docs/ESTIMATOR.md`](docs/ESTIMATOR.md) for the contracts and examples.

## Grounded research and cross-call context

- `POST /api/v1/research/intake/sessions/{id}/enrich` sends the accumulated vocal
  transcript and evidenced draft to GPT with web search, then persists a typed,
  cited context bundle. The browser lab invokes this automatically when intake is
  complete.
- Research is stored beside the spec, not inside its confirmed fields. It can suggest
  terminology, risks, assumptions to verify, and open vendor questions, but cannot
  silently become a customer fact, quote, benchmark, or competing-bid claim.
- At each call start, the backend snapshots the Estimator research plus up to eight
  earlier terminal calls for the same immutable spec. The snapshot contains stored
  transcript, quote, evidence, and outcome data so GPT can avoid failed questions and
  ask more accurate follow-ups.
- If a confirmed Estimator version has no stored research yet, call start first runs
  one idempotent Terra web-search pass over its evidenced fields and full stored
  ElevenLabs user transcript. A compact `pre_call_brief` is then sent to both the
  silent GPT adviser and the ElevenLabs Caller Agent before the voice connection is
  opened.
- The pre-call brief contains likely fee categories and safe conversation
  opportunities. `ask_vendor` opportunities remain questions;
  `frame_confirmed_fact` opportunities may use only the explicitly named confirmed
  fields.
- Prior-call prices are not leverage by default. Only an evidence-referenced bid in
  `approved_verified_leverage` authorizes the Caller to disclose a competing number.
- `POST /api/v1/reports/{version_id}/prepare` is the pre-report gate. It rejects
  nonterminal calls, performs a fresh web-search pass across the spec and completed
  call record, stores it, and returns the full report input context.
- `GET /api/v1/research/specs/{version_id}/report-context` returns the confirmed spec,
  initial research, completed calls, and latest final research without generating a
  ranking or inventing missing evidence.

For a browser test, start the API and open
`http://127.0.0.1:8000/demo/estimator.html`. The Estimator Lab provides editable
document fields and an ElevenLabs Agents voice-interview option. Both write into the
same draft and confirmation screen. After confirmation, **Continue to Caller Lab** starts the Caller
against that exact `version_id`; the lab verifies the stored canonical hash and shows
the Estimator facts and their provenance on its evidence board. The read-only
`GET /api/v1/intake/specs/{version_id}` endpoint supplies each value, modality,
confidence, and voice-turn/document-region/catalog reference before the call starts.

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
- The live browser Caller is an authenticated ElevenLabs Agent conversation. The
  ElevenLabs Agent owns listening, interruptions, conversational judgment, wording,
  and speech. GPT runs silently as a typed turn adviser: it extracts the latest
  evidence and recommends one objective, but does not return the spoken sentence.
- Caller prompting is split by responsibility: `elevenlabs_agent.txt` governs the
  live voice agent, `turn_adviser.txt` governs silent GPT decisions, and
  `negotiation_policy.txt` supplies shared ethical negotiation principles.
- Missing customer information follows a two-turn policy: request a provisional range,
  then capture exact callback requirements, then finalize a callback-required outcome.
- Explicit itemization refusals become transcript-backed `itemization_status=refused`.
  That closes the itemization objective, prevents paraphrased re-asking, and advances
  the Caller to total and fee clarification. A policy that requires itemization then
  finalizes the result as an incomplete quote rather than pressuring the vendor.
- The compatibility text/STT/TTS harness still validates GPT-authored spoken replies;
  the live Agent path instead records the exact ElevenLabs-authored utterance against
  the GPT advice ID that preceded it.
- Competing-bid leverage must be supplied as a verified, evidence-referenced policy
  input for the same immutable job. The Caller cannot select or invent leverage.
- A deterministic simulated voice adapter supports end-to-end development before
  ElevenLabs, Twilio, or SIP is connected.
- The local demo uses one ElevenLabs conversational Agent end to end. Two blocking
  client tools connect it to GPT decision advice and the Caller's evidence store.
- The compatibility voice endpoints remain latency-optimized: recording auto-sends after a short pause,
  the turn JSON returns as soon as GPT answers (`?tts=stream`), and speech is
  streamed from `GET /api/v1/demo/voice/sessions/{call_id}/speech` so playback
  starts on the first audio chunk. The fixed greeting audio is cached.

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
├── prompts/
│   ├── negotiation_policy.txt # Broad communication and negotiation policy
│   ├── text_buyer_agent.txt   # Planner-action surface realization
│   └── buyer_agent.txt        # Provider voice-agent tool prompt
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

To start the Estimator voice interview through ElevenLabs Agents, also configure the
Agent ID. The backend returns a short-lived signed WebSocket URL; it never sends the
ElevenLabs API key to the browser.

```powershell
$env:ELEVENLABS_INTAKE_AGENT_ID = "agent_your_intake_agent_id"
```

The Caller uses a separate ElevenLabs Agent. Configure its ID in the same server
terminal:

```powershell
$env:ELEVENLABS_CALLER_AGENT_ID = "agent_your_caller_agent_id"
```

Load `apps/api/app/caller/prompts/elevenlabs_agent.txt` into that Agent, set its First
message to `{{first_message}}`, and add blocking client tools named
`advise_caller_turn` and `record_caller_utterance`. Their exact parameter contracts
are in [`docs/ELEVENLABS_CALLER_AGENT.md`](docs/ELEVENLABS_CALLER_AGENT.md).

In the ElevenLabs agent dashboard, use
`apps/api/app/estimator/prompts/intake_agent.txt` as the intake instruction and add a
blocking client tool named `capture_intake_evidence`. Its parameters are
`user_text`, `field_name`, `value`, `mark_unknown`, and `unknown_acknowledged`.
The browser registers the tool and posts each supported answer to the Estimator's
evidence endpoint, then returns the deterministic planner's next question to the
agent. See `docs/ESTIMATOR.md` for the exact parameter contract.

If the live client tool does not complete, copy the finished conversation's `conv_…`
ID from ElevenLabs into **Post-call fallback** on the Home page. The API retrieves the
cloud transcript with `ELEVENLABS_API_KEY`, extracts fields with
`OPENAI_ESTIMATOR_MODEL` (falling back to `OPENAI_RESEARCH_MODEL`), and displays the
evidence for normal user confirmation. After confirmation, **Download Caller context
JSON** exports the immutable object that the Caller reads by `version_id`.

Without this value, the intake voice endpoint uses the simulated adapter for automated
tests; the browser clearly reports that live ElevenLabs Agents is not configured.

The keys stay server-side and are never sent to the browser. `OPENAI_MODEL` selects
the silent Caller adviser, while `OPENAI_RESEARCH_MODEL` selects the model used for
web-grounded research. The legacy STT/TTS compatibility harness uses
`scribe_v2`, and `eleven_flash_v2_5`:

```powershell
$env:OPENAI_MODEL = "gpt-5.4"
$env:OPENAI_RESEARCH_MODEL = "gpt-5.6-terra"
$env:ELEVENLABS_STT_MODEL = "scribe_v2"
$env:ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"
```

Start the local API:

```powershell
uvicorn apps.api.app.main:app --reload
```

Then open `http://127.0.0.1:8000/demo/` in a modern browser and select **Start
test call**. Start the embedded ElevenLabs conversation and speak as the vendor.
ElevenLabs conducts the conversation while GPT silently analyzes each vendor turn,
logs quote evidence, and advises the next objective.

Text input is available when voice + text is enabled in the ElevenLabs Agent's Widget
settings. It follows the same Agent and advisory tool loop.

The page includes a four-message quick test script that produces a complete quote.
You can also try phrases such as “I will call you back tomorrow” or “I decline to
quote” to exercise the other outcomes. With all three required environment values,
ElevenLabs owns the conversation and the OpenAI Responses API supplies same-turn
advice and structured fact extraction. Missing configuration produces a clear error
rather than silently switching the live page to a scripted caller.

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

## Conversation policy

`dialogue_planner.py` calculates the current objective, missing customer facts, vendor
requirements, quote progress, allowed actions, and one selected action before GPT is
called. `spoken_response.py` validates the resulting wording before ElevenLabs TTS.
The complete transcript and quote state remain the source of conversational memory.

The main spoken-turn acceptance rules are:

- no more than one question mark;
- no more than 45 words, with most planned turns targeting fewer than 30;
- no generic `Understood`/`Got it` opener;
- no near-duplicate of the previous buyer turn;
- one conversational objective per turn;
- an information blocker reaches a provisional quote, callback requirement, or
  incomplete outcome rather than looping.

The demo API returns `pipeline_timings_ms` for speech-to-text, GPT/Caller processing,
text-to-speech, and total turn time so latency can be measured rather than inferred
from transcript timestamps.

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
