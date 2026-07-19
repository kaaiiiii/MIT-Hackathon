# The Negotiator frontend

React, Vite, and React Router frontend integrated with the FastAPI Estimator,
research, Caller, and reporting-context APIs.

## Connected workflow

1. Home obtains microphone permission, creates an Estimator session, validates the
   required `disclosure` and `spoken_question` values, and obtains an ElevenLabs
   Agents signed URL.
2. The `capture_intake_evidence` client tool stores each transcript-backed field. If
   that live path fails, the post-call fallback imports a completed ElevenLabs
   `conversation_id`, verifies its session binding, and uses GPT to recover only
   verbatim user-spoken evidence into the same draft.
3. When the required field set closes, GPT-5.6 Terra web search runs and its summary,
   questions, citations, and limitations are displayed before confirmation.
4. Confirmation stores the immutable `version_id` locally, exposes Caller Lab, and
   enables download of the same immutable Caller-context JSON.
5. Caller Lab opens a signed connection to the dedicated ElevenLabs Caller Agent.
   The Agent owns the conversation and uses two blocking client tools for silent GPT
   decision advice and exact buyer-utterance recording.
6. Report loads persisted calls and research for that exact version. **Refresh calls**
   reloads evidence; **Prepare final report** requires terminal calls and runs the
   fresh pre-report research gate.

The bundled sample report and replay remain available when no backend version has
been confirmed.

## Development

Run FastAPI from the repository root:

```powershell
uvicorn apps.api.app.main:app --reload
```

Then run Vite in a second PowerShell window:

```powershell
cd apps/web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` and `/demo` to FastAPI on port
8000. Set `VITE_API_BASE` only when the API is hosted on a different origin.

## Production-style local run

```powershell
cd apps/web
npm ci
npm run build
cd ../..
uvicorn apps.api.app.main:app --reload
```

FastAPI detects `apps/web/dist`, serves the SPA at `http://127.0.0.1:8000/`, and
falls back to `index.html` for `/report` and `/call/:id` browser refreshes.

Checks:

```powershell
cd apps/web
npm run lint
npm run build
```
