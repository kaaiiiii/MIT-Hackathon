# ElevenLabs-owned Caller loop

The live Caller uses an ElevenLabs Agent for listening, conversational judgment,
wording, interruption handling, and speech. GPT is a silent decision adviser. The
backend remains authoritative for the immutable job, state machine, transcript,
quote evidence, and final outcome.

```text
Vendor speech
    -> ElevenLabs Agent hears the live turn
    -> advise_caller_turn (blocking client tool)
    -> GPT returns analysis + evidence extraction + one objective
    -> ElevenLabs chooses a natural response
    -> record_caller_utterance (blocking client tool)
    -> backend stores the exact intended spoken text
    -> ElevenLabs speaks it
```

GPT never returns a `spoken_response` in this path. Its `response_guidance` is not a
script. This prevents the browser/backend from assembling canned questions while
retaining GPT's deeper quote analysis and decision support.

## Environment

Set three server-side values and restart Uvicorn:

```powershell
$env:ELEVENLABS_API_KEY = "your_workspace_api_key"
$env:ELEVENLABS_CALLER_AGENT_ID = "agent_your_caller_agent_id"
$env:OPENAI_API_KEY = "your_openai_api_key"
uvicorn apps.api.app.main:app --reload
```

`ELEVENLABS_CALLER_AGENT_ID` is separate from
`ELEVENLABS_INTAKE_AGENT_ID`. The API key is used only by the backend to create a
short-lived signed URL and is never returned to the browser.

## ElevenLabs Agent configuration

1. Create or select a dedicated Caller Agent.
2. Use `apps/api/app/caller/prompts/elevenlabs_agent.txt` as its system prompt.
3. Set its First message to exactly `{{first_message}}`.
4. Enable the End Call system tool.
5. Add the following two **Client** tools. Enable **Wait for response** on both.

### `advise_caller_turn`

Description: Call exactly once after every vendor utterance and before deciding what
to say. It obtains silent GPT analysis and records transcript-backed vendor facts.

Parameters:

- `vendor_text`: string, required. Latest vendor utterance verbatim.
- `conversation_history`: string, optional. Configure this from
  `system__conversation_history` when the dashboard permits a dynamic value.

The result contains `advice_id`, `next_action`, `conversational_objective`,
`response_guidance`, `quote_so_far`, and any recommended terminal outcome.

### `record_caller_utterance`

Description: Call immediately before speaking every response after the opening. The
agent must pass the exact text it will say and the advice ID that led to it.

Parameters:

- `spoken_text`: string, required.
- `advice_id`: string, required.

The backend rejects missing, unknown, reused, or cross-call advice IDs. This makes
each recorded buyer response traceable to one GPT advisory decision without letting
GPT control the spoken wording.

## Local test

Build the React frontend if needed, start Uvicorn, and open `/calls` or `/demo/`.
Select **Start test call**, then start the ElevenLabs conversation in the embedded
panel and speak as the vendor. Quote facts appear on the evidence board when the
adviser extracts them. The call finalizes only through the backend outcome validator.

The older text/STT/TTS endpoints remain available as a compatibility harness for
automated tests, but `/demo/` now uses the ElevenLabs Agent loop.
