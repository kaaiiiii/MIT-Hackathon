# Product context: The Negotiator

This repository implements the Caller portion of the ElevenLabs "The Negotiator"
challenge brief. Treat the challenge brief as the authoritative product context; this
file keeps its implementation constraints close to the code.

## Product goal

Build an end-to-end voice-agent system that creates one confirmed job specification,
calls multiple vendors with exactly that specification, captures comparable itemized
quotes, negotiates using only real leverage, and reports a ranked recommendation with
recording and transcript evidence.

The current vertical is moving services. Vertical-specific job fields, benchmarks,
red-flag rules, and negotiation levers should become configuration rather than Caller
code.

## Caller requirements

- Conduct a real voice conversation. Voice is the trust mechanism, not a presentation
  layer over a post-call extraction workflow.
- Reuse the confirmed specification verbatim and never invent or modify job facts.
- Disclose that the caller is an AI assistant acting for a buyer, including when asked
  directly whether it is a robot.
- Handle interruptions, vague answers, refusals, hard selling, hang-ups, and callback
  promises without fabricating a result.
- Choose one deterministic conversational objective per turn; GPT controls surface
  wording, not the complete call strategy.
- Resolve a genuinely missing customer fact within two buyer turns by seeking a
  provisional range, capturing callback requirements, or ending with an explicit
  incomplete outcome.
- Log important quote facts while the conversation is happening, with vendor transcript
  evidence attached to every stored claim.
- End every call with a structured outcome: itemized quote, callback commitment,
  documented decline, or explicitly incomplete quote.
- Keep negotiation leverage honest. Never invent inventory, a bid, or a price benchmark.
- Preserve recordings and transcripts for evaluation and final reporting.

## Demo success criteria that affect this module

- Demonstrate live calls against at least three distinct vendor/negotiation styles.
- Capture every quote in a structured comparable form with fees itemized.
- Show at least one price or term changing because of real, previously gathered leverage.
- Make AI disclosure, honesty constraints, and failure handling visible in the demo.
- Use golden calls/evaluation cases to check fee extraction and red-flag behavior.

## Full-program boundary

The complete challenge also requires Estimator intake by ElevenLabs voice interview and
at least one document type, plus a Closer that negotiates and ranks evidence-backed
quotes. Those are adjacent modules. The isolated Caller must expose clean inputs and
outputs to them, but must not absorb intake truth, market benchmarking, vendor ranking,
or report generation.
