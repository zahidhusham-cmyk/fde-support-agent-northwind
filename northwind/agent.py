# this is the support agent which will help diagnose and fix issues with the syste
# note some loop function will be passing the ticket to the agent as a parameter. That is TO BE DONE.

import asyncio
import json
import os
import re
import sys
import time  # telemetry: tool-call duration

import httpx2

from northwind.carrier_tools import TOOLS, process_tool_call
from northwind.customer_actions import process_customer_action
from northwind.integrations import load_env
from northwind.trace import record_message, record_start_run, record_tool_call  # telemetry

CHAT_INSTRUCTIONS = """
You are a Northwind Mobile support agent. Customers open tickets because their mobile data is not working. You can see and change the carrier side only: the customer record, bills, the line, the network view, and tower outages. You cannot see or change the phone. Airplane mode, mobile data, data roaming, a restart, a loose SIM, and the handset APN are the customer's to check and fix. Ask them to do those, then call get_network_status again. Do not trust a claim that conflicts with the network view.

The ticket text includes the incident number, the customer id (CUS-...), and the line id (LIN-...). Use those ids in tools.

Your first message only greets the customer, restates the ticket in plain language, and asks if they want to start. Do not call tools in that first message.

After they agree, investigate before you guess:
- get_line_status and get_network_status for the line.
- check_area_outages if the phone is not registered. If last_cell_id is a tower in that list, tell them it is an outage and stop. Do not send them through phone settings.
- get_billing before resume_line. A fraud_hold cannot be lifted. A non_payment suspension stays until the overdue bill is paid with pay_bill.
- add_work_note on the incident number when you find the cause or change the line. The customer does not see work notes.
- The network view shows symptoms, not causes. Not registered can be airplane mode, a restart, a loose SIM, or the phone being off. Ask one clear question at a time.

When the data session is active and the line is in good standing, tell them it is fixed and call resolve_ticket with the incident number and a short resolution that names the cause. For a tower outage, tell them and resolve the same way, and include the word outage in the resolution. If you cannot fix it, say you are handing it to a person. Do not resolve a fraud hold or hardware damage.
"""

XAI_URL = "https://api.x.ai/v1/chat/completions"
MAX_TURNS = 20


def _read_reply(prompt: str = "> ") -> str:
    """Read one line from the terminal keyboard.

    input() reads sys.stdin on the asyncio thread. Inside asyncio.run that
    thread owns the signal handler, and this terminal never delivers normal
    keystrokes to that read. /dev/tty is the keyboard, read from a worker thread.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        with open("/dev/tty", "r", encoding="utf-8", errors="replace") as tty:
            line = tty.readline()
    except OSError:
        line = sys.stdin.readline()
    if not line:
        raise EOFError("no reply")
    return line.strip()


def _line_id(ticket: str) -> str | None:
    """The LIN-... id written into the ServiceNow description."""
    match = re.search(r"LIN-\d+", ticket or "")
    return match.group(0) if match else None


def _apply_customer_reply(reply: str, line_id: str | None) -> None:
    """If the reply is an exact phone action, PATCH the device as the customer.

    Any other sentence returns None and is left for the agent. The typed text
    still goes to the model either way.
    """
    result = process_customer_action(reply, line_id or "")
    if result:
        print(result, flush=True)


async def ask_question(agent_message: str) -> str:
    """Print one thing the model said and block until the user answers.

    This does not call the model. load_ticket decides when a reply is for the
    user, then passes that text here. The same function is used for the
    greeting and for every later question.
    """
    print(agent_message, flush=True)
    print(flush=True)
    return await asyncio.to_thread(_read_reply, "> ")


async def complete(
    client: httpx2.AsyncClient,
    api_key: str,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """One Grok call. Returns the assistant message dict (role, content, maybe tool_calls)."""
    body = {
        "model": os.getenv("GROK_MODEL", "grok-4"),
        "messages": messages,
    }

    if tools:
        body["tools"] = tools

    response = await client.post(
        XAI_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]


def apply_tool_calls(messages: list[dict], assistant: dict, run_id: str) -> None:  # telemetry: run_id
    """Run every tool call on this assistant turn and append the JSON results.

    The chat API sends each call as function.name plus function.arguments (a JSON
    string). process_tool_call expects name and a parameters dict, and returns
    the string that goes in the tool message.
    """
    for call in assistant["tool_calls"]:
        function = call.get("function") or {}
        name = function.get("name") or call.get("name")
        raw_arguments = function.get("arguments", call.get("parameters") or {})
        if isinstance(raw_arguments, str):
            try:
                parameters = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                parameters = None
        else:
            parameters = raw_arguments
        started = time.perf_counter()  # telemetry
        if parameters is None:
            content = json.dumps({"error": f"could not parse arguments for {name}"})
        else:
            content = process_tool_call({"name": name, "parameters": parameters})
        latency_ms = int((time.perf_counter() - started) * 1000)  # telemetry
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id"),
            "content": content,
        })
        # telemetry: one run_steps row for this tool call
        record_tool_call({
            "run_id": run_id,
            "name": name,
            "parameters": parameters,
            "result": content,
            "latency": latency_ms,
        })


def _trace_row(ticket: str) -> dict:  # telemetry
    """Fields for the runs row. A hand-written ticket may have no incident number."""
    incident_number = None
    correlation_id = None
    first = (ticket or "").splitlines()[0]
    if first.startswith("Incident ") and first.endswith(")") and "(" in first:
        head, _, tail = first.partition("(")
        incident_number = head.removeprefix("Incident ").strip()
        correlation_id = tail[:-1].strip()
    return {
        "incident_number": incident_number,
        "correlation_id": correlation_id,
        "model": os.getenv("GROK_MODEL", "grok-4"),
    }


async def load_ticket(ticket: str):
    """Greet once, wait until the user starts, then investigate.

    An outer loop will pass the ticket in. The greeting is a single call outside
    the loop. The loop is the investigation: tool calls run there without
    waiting, and questions to the user still go through ask_question.
    """
    load_env()
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set. Add it to .env.")

    messages = [
        {"role": "system", "content": CHAT_INSTRUCTIONS},
        {"role": "user", "content": ticket},
    ]

    # telemetry: open the runs row, then store the ticket as the first user step
    row = _trace_row(ticket)
    run_id = record_start_run(row)
    record_message(run_id, "user", ticket)
    line_id = _line_id(ticket)

    async with httpx2.AsyncClient(timeout=60) as client:
        # One greeting. No tools, and not part of the investigation loop.
        greeting = await complete(client, api_key, messages)
        messages.append(greeting)
        greeting_text = (greeting.get("content") or "").strip()
        if not greeting_text:
            raise RuntimeError("Model returned no greeting.")
        record_message(run_id, "agent", greeting_text)  # telemetry
        started = await ask_question(greeting_text)
        record_message(run_id, "user", started)  # telemetry
        messages.append({"role": "user", "content": started})

        for _ in range(MAX_TURNS):
            assistant = await complete(client, api_key, messages, tools=TOOLS)
            messages.append(assistant)
            # Tool calls stay in this loop and do not wait on the user.
            if assistant.get("tool_calls"):
                apply_tool_calls(messages, assistant, run_id)  # telemetry: run_id is only for the trace
                continue
            text = (assistant.get("content") or "").strip()
            if not text:
                raise RuntimeError("Model returned no text to show the user.")
            record_message(run_id, "agent", text)  # telemetry
            answer = await ask_question(text)
            _apply_customer_reply(answer, line_id)
            record_message(run_id, "user", answer)  # telemetry
            messages.append({"role": "user", "content": answer})
    raise RuntimeError(f"Stopped after {MAX_TURNS} investigation turns.")
