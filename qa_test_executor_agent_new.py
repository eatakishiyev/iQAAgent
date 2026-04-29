# telco_qa_executor.py
from __future__ import annotations

import json

from time import sleep
from typing import TypedDict
from langgraph.checkpoint.memory import MemorySaver
from openai import OpenAI
from langgraph.types import Command

from graph_builder import build_graph
from iQAProject.flow_validator import validate_flow
from iQAProject.variables import INFERENCE_URL, MODEL_NAME

# Raw OpenAI-compatible client — used by generator agent for chat completions
openai_client = OpenAI(
    base_url = INFERENCE_URL,
    api_key  = "EMPTY",
)




# ══════════════════════════════════════════════════════════════════
#  GENERATOR AGENT  (uses raw openai_client — no LangChain needed)
# ══════════════════════════════════════════════════════════════════

with open("config/agents/test-generator-agent/SKILL.md", "r") as file:
    GENERATOR_SYSTEM = file.read()


def generate_flow(user_prompt: str, max_retries: int = 3) -> dict:
    """Generate and validate a flow from natural language using the local LLM."""
    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM},
        {"role": "user",   "content": user_prompt},
    ]

    for attempt in range(1, max_retries + 1):
        print(f"\n[GENERATOR] Attempt {attempt}/{max_retries}")

        resp = openai_client.chat.completions.create(
            model       = MODEL_NAME,
            messages    = messages,
            temperature = 0.0,
            extra_body  = {
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        raw = resp.choices[0].message.content.strip()
        print(f"  [GENERATOR] Flow details:\n{raw}")
        # Strip accidental markdown fences
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.split("```")[0].strip()

        try:
            flow = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  [GENERATOR] JSON parse error: {e}")
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user",      "content": f"Invalid JSON: {e}. Return only valid JSON."},
            ]
            continue

        errors = validate_flow(flow)
        if not errors:
            print(f"  [GENERATOR] Valid — {flow.get('flow_id','?')}, "
                  f"{len(flow['nodes'])} nodes")
            return flow

        error_text = "\n".join(f"- {e}" for e in errors)
        print(f"  [GENERATOR] {len(errors)} error(s):\n{error_text}")
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user",      "content":
                f"Fix these {len(errors)} error(s) and return corrected JSON:\n{error_text}"},
        ]

    raise RuntimeError(f"Flow generation failed after {max_retries} attempts")


# ══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════

def start_test(flow: dict, checkpointer) -> dict:
    errors = validate_flow(flow)
    if errors:
        raise ValueError("Invalid flow:\n" + "\n".join(errors))

    graph  = build_graph(flow, checkpointer)
    config = {"configurable": {"thread_id": flow["flow_id"]}}

    print(f"\n[EXECUTOR] Starting: {flow['flow_id']}")
    return graph.invoke(
        {
            "flow":        flow,
            "variables":   dict(flow.get("variables", {})),
            "last_event":  {},
            "failed":      False,
            "fail_reason": "",
        },
        config=config,
    )


def resume_test(flow: dict, checkpointer, event_payload: dict) -> dict:
    graph  = build_graph(flow, checkpointer)

    config = {"configurable": {"thread_id": flow["flow_id"]}}

    state = graph.get_state(config)
    for interrupt in state.interrupts:
        if interrupt.value["waiting_for"] == event_payload["event"]:
            print(f"\n[EXECUTOR] Resuming: {flow['flow_id']} with {event_payload}")
            return graph.invoke(Command(resume=event_payload), config=config)
    print(f"[RESUME] Received event is not eligible to proceed next: {flow['flow_id']} with {event_payload}]")
    return state


def run_test_from_prompt(
    user_prompt:     str,
    checkpointer,
    review_callback = None,
) -> dict:
    print("\n══ STEP 1: Generate flow ══")
    flow = generate_flow(user_prompt)

    if review_callback is not None:
        print("\n══ STEP 2: Human review ══")
        if not review_callback(flow):
            raise RuntimeError("Flow rejected — execution aborted.")
        print("  [REVIEW] Approved")

    print("\n══ STEP 3: Execute ══")
    state = start_test(flow, checkpointer)
    return {"flow": flow, "state": state}


checkpointer = MemorySaver()

# ── One-liner from prompt ──────────────────────────────────────────────
result = run_test_from_prompt(
    user_prompt="""
                      - Make call from 994702011342 to 994501021231 with duration 45 seconds;
                      - When subscriber answered send SMS to 994776011342 with text "+Roaming";
                      - If balance is less than 10 AZN then topup 20 AZN.;
                      - If balance updated continue call else finish testing;
                      - Then make call from 994702011342 to 994501021231 with duration 60 seconds;
                      - Wait for call to end;
                      - Send SMS to 994776011342 from 994702011342 with text "+Roaming";
    """,
    checkpointer=checkpointer,
)

# ── Resume when telco engine fires call_finished ───────────────────────
sleep(20)
resume_test(
    flow          = result["flow"],
    checkpointer  = checkpointer,
    event_payload = {"event":"call_answered","call_id": "c_abc123", "charged": True, "actual_duration": 45},
)


sleep(10)
result = resume_test(
    flow          = result["flow"],
    checkpointer  = checkpointer,
    event_payload = {"event":"call_finisheds","call_id": "c_mock_001", "charged": True, "actual_duration": 45},
)

print(result)
