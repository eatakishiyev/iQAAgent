# telco_qa_executor.py
from __future__ import annotations

import json
import re
from time import sleep
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command


# ══════════════════════════════════════════════════════════════════
#  LOCAL LLM — shared across generator and executor
# ══════════════════════════════════════════════════════════════════

INFERENCE_URL = "http://localhost:1234/v1"
MODEL_NAME    = "qwen/qwen3.5-9b"

# LangChain LLM — used by executor nodes for tool calls
llm = ChatOpenAI(
    base_url         = INFERENCE_URL,
    api_key          = "EMPTY",
    model            = MODEL_NAME,
    reasoning_effort = None,
    reasoning        = None,
    temperature      = 0.0,
    model_kwargs     = {
        "extra_body": {
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    },
    streaming = True,
)

# Raw OpenAI-compatible client — used by generator agent for chat completions
openai_client = OpenAI(
    base_url = INFERENCE_URL,
    api_key  = "EMPTY",
)


# ══════════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════════

class FlowState(TypedDict):
    flow:        dict
    variables:   dict
    last_event:  dict
    failed:      bool
    fail_reason: str


# ══════════════════════════════════════════════════════════════════
#  TOOLS
# ══════════════════════════════════════════════════════════════════

@tool
def make_call(caller_number: str, callee_number: str, duration_seconds: int) -> dict:
    """Simulate a GSM CAMEL call toward SCP."""
    # return telco_engine.simulate_call(caller_number, callee_number, duration_seconds)
    return {"call_id": "c_mock_001", "status": "completed",
            "actual_duration": duration_seconds, "charged": True}

@tool
def get_balance(msisdn: str) -> dict:
    """Query subscriber balance from billing system."""
    # return telco_engine.check_balance(msisdn)
    return {"balance": 7.50, "currency": "AZN", "package_active": False}

@tool
def topup_balance(msisdn: str, amount_azn: float) -> dict:
    """Top up subscriber balance."""
    # return telco_engine.topup(msisdn, amount_azn)
    return {"success": True, "new_balance": 7.50 + amount_azn}

@tool
def send_sms(sender_number: str, recipient_number: str, message_body: str) -> dict:
    """Send SMS via SMSGW."""
    # return telco_engine.send_sms(sender_number, recipient_number, message_body)
    return {"message_id": "sms_mock_001", "status": "delivered"}

@tool
def activate_package(msisdn: str, package_code: str) -> dict:
    """Activate a tariff package for a subscriber."""
    # return telco_engine.activate_package(msisdn, package_code)
    return {"success": True, "package_id": "pkg_mock_001",
            "valid_until": "2026-12-31T23:59:59"}

TOOL_MAP = {
    "make_call":        make_call,
    "get_balance":      get_balance,
    "topup_balance":    topup_balance,
    "send_sms":         send_sms,
    "activate_package": activate_package,
}


# ══════════════════════════════════════════════════════════════════
#  PLACEHOLDER RESOLVER
# ══════════════════════════════════════════════════════════════════

def resolve(value: Any, variables: dict) -> Any:
    if isinstance(value, str):
        def replacer(match):
            name = match.group(1)
            if name not in variables:
                raise KeyError(
                    f"Variable '${{{name}}}' not defined. "
                    f"Available: {list(variables.keys())}"
                )
            return str(variables[name])
        return re.sub(r"\$\{(\w+)\}", replacer, value)
    if isinstance(value, dict):
        return {k: resolve(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, variables) for v in value]
    return value


def safe_eval(expression: str, variables: dict) -> bool:
    resolved = resolve(expression, variables)
    return bool(eval(resolved, {"__builtins__": {}}, variables))


# ══════════════════════════════════════════════════════════════════
#  NODE FACTORIES
# ══════════════════════════════════════════════════════════════════

def make_action_node(node_def: dict):
    node_id   = node_def["id"]
    tool_name = node_def["tool"]
    store_as  = node_def.get("store_as", {})
    assert_   = node_def.get("assert")

    def node(state: FlowState) -> dict:
        if state.get("failed"):
            return {}

        params = resolve(node_def.get("params", {}), state["variables"])

        # Bind exactly one tool so the local LLM cannot hallucinate others
        bound = llm.bind_tools([TOOL_MAP[tool_name]])
        resp  = bound.invoke([HumanMessage(content=(
            f"Execute tool '{tool_name}' with these exact parameters:\n"
            f"{json.dumps(params, indent=2)}\n"
            f"Call the tool once and return the result."
        ))])

        result: dict = {}
        for tc in resp.tool_calls:
            if tc["name"] == tool_name:
                raw    = TOOL_MAP[tool_name].invoke(tc["args"])
                result = json.loads(raw) if isinstance(raw, str) else raw
                break

        result_dict  = result if isinstance(result, dict) else {}
        updated_vars = {**state["variables"]}

        for src, dst in store_as.items():
            if src in result_dict:
                updated_vars[dst] = result_dict[src]

        updated_vars.update(result_dict)

        print(f"  [ACTION] {node_id} ({tool_name}) → {result_dict}")

        if assert_:
            try:
                passed = safe_eval(assert_["condition"], updated_vars)
            except Exception as e:
                passed = False
                print(f"  [ASSERT ERROR] {node_id}: {e}")

            if not passed:
                msg = assert_.get("message", f"Assert failed on '{node_id}'")
                print(f"  [ASSERT FAIL] {msg}")
                return {
                    "variables":   updated_vars,
                    "failed":      True,
                    "fail_reason": f"[{node_id}] {msg}",
                }

        return {"variables": updated_vars}

    node.__name__ = node_id
    return node


def make_wait_node(node_def: dict):
    node_id = node_def["id"]
    event   = node_def.get("event", "external_event")

    def node(state: FlowState) -> dict:
        if state.get("failed"):
            return {}

        print(f"  [WAIT] {node_id} — suspended, waiting for: {event}")

        event_payload = interrupt({
            "node_id":     node_id,
            "waiting_for": event,
        })

        updated_vars = {**state["variables"]}
        if isinstance(event_payload, dict):
            updated_vars.update(event_payload)
            print(f"  [WAIT] {node_id} — resumed with: {event_payload}")

        return {
            "variables":  updated_vars,
            "last_event": event_payload or {},
        }

    node.__name__ = node_id
    return node


def make_gateway_router(node_def: dict):
    node_id  = node_def["id"]
    branches = node_def["branches"]

    def router(state: FlowState) -> str:
        if state.get("failed"):
            return "__fail__"

        for branch in branches:
            condition = branch["condition"]
            if condition == "default":
                print(f"  [GATEWAY] {node_id} → default → {branch['next']}")
                return branch["next"]
            try:
                if safe_eval(condition, state["variables"]):
                    print(f"  [GATEWAY] {node_id} → '{condition}' → {branch['next']}")
                    return branch["next"]
            except Exception as e:
                print(f"  [GATEWAY] condition error '{condition}': {e}")

        raise ValueError(
            f"Gateway '{node_id}': no branch matched. "
            f"Add a default branch."
        )

    router.__name__ = f"route_{node_id}"
    return router


# ══════════════════════════════════════════════════════════════════
#  VALIDATOR
# ══════════════════════════════════════════════════════════════════

def validate_flow(flow: dict) -> list[str]:
    errors   = []
    nodes    = flow.get("nodes", [])
    node_ids = {n["id"] for n in nodes}

    if not any(n["type"] == "start" for n in nodes):
        errors.append("No start node found")
    if not any(n["type"] == "end" for n in nodes):
        errors.append("No end node found")

    for n in nodes:
        nid, ntype = n.get("id", "?"), n.get("type", "?")

        if ntype == "action":
            if n.get("tool") not in TOOL_MAP:
                errors.append(f"Node '{nid}': unknown tool '{n.get('tool')}'")
            if n.get("next") not in node_ids:
                errors.append(f"Node '{nid}': 'next' → unknown '{n.get('next')}'")

        elif ntype == "wait":
            if n.get("next") not in node_ids:
                errors.append(f"Node '{nid}': 'next' → unknown '{n.get('next')}'")

        elif ntype == "gateway":
            branches = n.get("branches", [])
            if not branches:
                errors.append(f"Node '{nid}': gateway has no branches")
            if branches and branches[-1].get("condition") != "default":
                errors.append(f"Node '{nid}': last branch must be 'default'")
            for b in branches:
                if b.get("next") not in node_ids:
                    errors.append(f"Node '{nid}': branch → unknown '{b.get('next')}'")

        elif ntype == "start":
            if n.get("next") not in node_ids:
                errors.append(f"Start 'next' → unknown '{n.get('next')}'")

    return errors


# ══════════════════════════════════════════════════════════════════
#  DYNAMIC GRAPH BUILDER
# ══════════════════════════════════════════════════════════════════

def build_graph(flow: dict, checkpointer) -> any:
    nodes   = flow["nodes"]
    builder = StateGraph(FlowState)

    for nd in nodes:
        nid, ntype = nd["id"], nd["type"]

        if ntype == "start":
            def _start(state: FlowState, _id=nid) -> dict:
                print(f"  [START] {state['flow'].get('flow_id', '?')}")
                return {}
            _start.__name__ = nid
            builder.add_node(nid, _start)

        elif ntype == "action":
            builder.add_node(nid, make_action_node(nd))

        elif ntype == "wait":
            builder.add_node(nid, make_wait_node(nd))

        elif ntype == "gateway":
            def _gw(state: FlowState, _id=nid) -> dict:
                return {}
            _gw.__name__ = nid
            builder.add_node(nid, _gw)

        elif ntype == "end":
            def _end(state: FlowState, _nd=nd) -> dict:
                if state.get("failed"):
                    print(f"  [END] FAILED — {state.get('fail_reason', '')}")
                else:
                    r = _nd.get("result", {})
                    print(f"  [END] {r.get('status','?').upper()} — {r.get('summary','')}")
                return {}
            _end.__name__ = nid
            builder.add_node(nid, _end)

    def _fail(state: FlowState) -> dict:
        print(f"  [FAIL] {state.get('fail_reason', 'assertion failed')}")
        return {}
    builder.add_node("__fail__", _fail)

    for nd in nodes:
        nid, ntype = nd["id"], nd["type"]

        if ntype == "start":
            builder.set_entry_point(nid)
            builder.add_edge(nid, nd["next"])

        elif ntype == "action":
            next_id = nd["next"]
            builder.add_conditional_edges(
                nid,
                lambda s, _n=next_id: "__fail__" if s.get("failed") else _n,
                {"__fail__": "__fail__", next_id: next_id},
            )

        elif ntype == "wait":
            builder.add_edge(nid, nd["next"])

        elif ntype == "gateway":
            router  = make_gateway_router(nd)
            targets = {b["next"]: b["next"] for b in nd["branches"]}
            targets["__fail__"] = "__fail__"
            builder.add_conditional_edges(nid, router, targets)

        elif ntype == "end":
            builder.add_edge(nid, END)

    builder.add_edge("__fail__", END)
    return builder.compile(checkpointer=checkpointer)


# ══════════════════════════════════════════════════════════════════
#  GENERATOR AGENT  (uses raw openai_client — no LangChain needed)
# ══════════════════════════════════════════════════════════════════

GENERATOR_SYSTEM = """You are a JSON test flow generator for a Telecom QA system.

## YOUR ONLY JOB
Convert the user's test description into a JSON object that follows the schema below.
Output ONLY the raw JSON. No explanation. No markdown. No code fences. No preamble.

## OUTPUT FORMAT
Your entire response must be a single JSON object starting with { and ending with }.
Do not write anything before { or after }.

## SCHEMA
{
  "flow_id": "short_snake_case_id",
  "name": "human readable name",
  "description": "what this test validates",
  "version": "1.0.0",
  "variables": {
    "variable_name": "initial_value"
  },
  "nodes": [ ...array of node objects... ]
}

## NODE TYPES — use exactly these types, no others

### start node (exactly one required)
{"id": "start", "type": "start", "next": "<id of first action node>"}

### action node
{
  "id": "unique_snake_case_id",
  "type": "action",
  "tool": "<tool name from list below>",
  "params": {"param_name": "value or ${variable_name}"},
  "store_as": {"result_key": "variable_name"},
  "next": "<id of next node>"
}
Note: store_as is optional. Use it to rename result keys.

### wait node
{
  "id": "unique_snake_case_id",
  "type": "wait",
  "event": "<event name from list below>",
  "timeout_seconds": 120,
  "on_timeout": "<id of end node>",
  "next": "<id of next node after event received>"
}

### gateway node
{
  "id": "unique_snake_case_id",
  "type": "gateway",
  "branches": [
    {"condition": "Python expression e.g. ${balance} < 10", "next": "<node id>"},
    {"condition": "default", "next": "<node id>"}
  ]
}
RULE: branches are evaluated top to bottom. Last branch MUST have condition "default".

### end node (at least one required)
{
  "id": "unique_snake_case_id",
  "type": "end",
  "result": {"status": "passed", "summary": "what happened"}
}

## AVAILABLE TOOLS — use ONLY these exact names

make_call
  params:  caller_number (string), callee_number (string), duration_seconds (integer)
  returns: call_id, status (completed|failed|busy), actual_duration, charged (boolean)

get_balance
  params:  msisdn (string)
  returns: balance (number in AZN), currency, package_active (boolean)

topup_balance
  params:  msisdn (string), amount_azn (number)
  returns: success (boolean), new_balance (number)

send_sms
  params:  sender_number (string), recipient_number (string), message_body (string)
  returns: message_id, status (delivered|failed)

activate_package
  params:  msisdn (string), package_code (string)
  returns: success (boolean), package_id, valid_until (ISO datetime)

## AVAILABLE WAIT EVENTS
call_finished | sms_delivered | topup_complete | manual

## VARIABLE REFERENCES
Use ${variable_name} inside param string values to reference earlier results.
Example: "msisdn": "${subscriber_a}"

## RULES — follow all of these strictly
1. Every node id must be unique and use snake_case
2. Every node except end must have a "next" field (or "branches" for gateway)
3. Every "next" value must match an existing node id
4. Every tool name must be from the AVAILABLE TOOLS list above
5. Gateway must always have "default" as the last branch condition
6. Use store_as when calling the same tool more than once to avoid variable name collision
7. Add a wait node after make_call to wait for call_finished event
8. Always include at least one end node

## EXAMPLE OUTPUT
{
  "flow_id": "prepaid_call_test",
  "name": "Prepaid call deduction test",
  "description": "Verify balance decreases after a call",
  "version": "1.0.0",
  "variables": {
    "subscriber_a": "994702011342",
    "subscriber_b": "994501021231"
  },
  "nodes": [
    {"id": "start", "type": "start", "next": "make_call_step"},
    {
      "id": "make_call_step",
      "type": "action",
      "tool": "make_call",
      "params": {
        "caller_number": "${subscriber_a}",
        "callee_number": "${subscriber_b}",
        "duration_seconds": 40
      },
      "store_as": {"call_id": "call_1_id"},
      "next": "wait_call"
    },
    {
      "id": "wait_call",
      "type": "wait",
      "event": "call_finished",
      "timeout_seconds": 120,
      "on_timeout": "end_fail",
      "next": "check_balance"
    },
    {
      "id": "check_balance",
      "type": "action",
      "tool": "get_balance",
      "params": {"msisdn": "${subscriber_a}"},
      "next": "balance_check_gateway"
    },
    {
      "id": "balance_check_gateway",
      "type": "gateway",
      "branches": [
        {"condition": "${balance} < 0", "next": "end_fail"},
        {"condition": "default", "next": "end_pass"}
      ]
    },
    {
      "id": "end_pass",
      "type": "end",
      "result": {"status": "passed", "summary": "Balance deducted correctly"}
    },
    {
      "id": "end_fail",
      "type": "end",
      "result": {"status": "failed", "summary": "Test failed"}
    }
  ]
}"""


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

    print(f"\n[EXECUTOR] Resuming: {flow['flow_id']} with {event_payload}")
    return graph.invoke(Command(resume=event_payload), config=config)


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
    Test prepaid voice deduction for subscriber 994702011342:
    - Call 994501021231 for 45 seconds
    - After call ends, check balance
    - If balance < 10 AZN, top up by 20 AZN
    - Make a second 60-second call to verify
    - Fail if any call is not charged
    """,
    checkpointer=checkpointer,
)

# ── Resume when telco engine fires call_finished ───────────────────────
sleep(60)
resume_test(
    flow          = result["flow"],
    checkpointer  = checkpointer,
    event_payload = {"call_id": "c_abc123", "charged": True, "actual_duration": 45},
)