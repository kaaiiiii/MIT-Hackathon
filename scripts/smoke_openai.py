"""One-turn OpenAI connectivity check; never prints credentials."""

from __future__ import annotations

import asyncio
import json
import os

from apps.api.app.caller.adapters.simulated import SimulatedVoiceSessionAdapter
from apps.api.app.caller.demo import DEMO_SPEC, DEMO_VENDOR, TextVoiceSimulator
from apps.api.app.caller.input_gateway import InMemoryCallerInputGateway
from apps.api.app.caller.openai_agent import OpenAIBuyerTurnModel
from apps.api.app.caller.orchestrator import CallOrchestrator
from apps.api.app.caller.persistence import SQLiteCallerStore


async def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured")

    store = SQLiteCallerStore(":memory:")
    try:
        orchestrator = CallOrchestrator(
            store,
            InMemoryCallerInputGateway([DEMO_SPEC], [DEMO_VENDOR]),
            SimulatedVoiceSessionAdapter(),
        )
        model = OpenAIBuyerTurnModel(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            job_facts=DEMO_SPEC.facts,
        )
        simulator = TextVoiceSimulator(orchestrator, model)
        started = await simulator.start()
        result = await simulator.receive(
            started.call.call.call_id,
            "Yes, I can discuss the job. Please tell me the confirmed details.",
        )
        quote_turn = await simulator.receive(
            started.call.call.call_id,
            "We use a flat bundled rate. Labor is $540, the stair charge is $100, "
            "and the total is $640.",
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "model": quote_turn.model,
                    "call_status": quote_turn.call.call.status.value,
                    "response_received": bool(quote_turn.agent_message.strip()),
                    "line_items_captured": len(
                        quote_turn.call.original_quote.line_items
                    ),
                    "terms_captured": len(quote_turn.call.original_quote.terms),
                }
            )
        )
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
