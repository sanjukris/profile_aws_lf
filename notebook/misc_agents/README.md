A2A Profile Agents + Orchestrator (Quick Guide)
===============================================

What this folder contains
- Profile Strands Agent (A2A JSON‑RPC): `profile-strands-agent.py`
- Profile LangGraph Agent (A2A over HTTP): `profile-langgraph-agent.py`
- Orchestrator (Strands) using “A2A Agent‑as‑a‑Tool”: `orchestrator-agent.py`
- Simple A2A client for local testing: `a2a_client.py`

Why these are fast (design summary)
- Strands Profile Agent
  - Exposes a standard A2A JSON‑RPC server via `A2AServer`.
  - Adds a “fast‑path” middleware that intercepts JSON‑RPC `message/send` and directly
    calls the deterministic tool (no LLM planning loop). Still returns a valid A2A
    Message with `role="agent"` and a new `messageId`.
- LangGraph Profile Agent
  - Exposes HTTP transport: `/.well-known/agent-card.json` and `POST /a2a/messages`.
  - Returns structured JSON immediately (either from your existing LangGraph graph if
    present, or a deterministic stub), so latency stays low.
- Orchestrator
  - Caches the remote agent card once (“Agent‑as‑a‑Tool” pattern), reads
    `preferredTransport`, and calls the remote using the minimal path:
    - JSON‑RPC → A2A ClientFactory `send_message`
    - HTTP → `POST /a2a/messages`
  - Calls the tool directly (without an intermediate LLM) to return the raw structured
    JSON exactly as produced by the remote agent.

Protocol alignment (A2A)
- Discovery: `/.well-known/agent-card.json` advertises the transport and URL.
- Messaging:
  - Strands agent (JSON‑RPC): JSON‑RPC `message/send` at the root (`POST /`).
  - LangGraph agent (HTTP): `POST /a2a/messages` with an A2A Message payload.

Run locally (separate terminals)
1) Strands Profile Agent (JSON‑RPC)
   - `python notebook/misc_agents/profile-strands-agent.py`
   - Agent card: `curl http://127.0.0.1:9003/.well-known/agent-card.json`

2) LangGraph Profile Agent (HTTP)
   - `python notebook/misc_agents/profile-langgraph-agent.py`
   - Agent card: `curl http://127.0.0.1:9004/.well-known/agent-card.json`

3) Orchestrator
   - Against Strands agent (default):
     - `python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"`
   - Against LangGraph agent:
     - `export PROFILE_AGENT_URL=http://127.0.0.1:9004/`
     - `python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"`

Testing with curl
- Strands (JSON‑RPC):
  - `curl -sS -X POST http://127.0.0.1:9003/ -H 'Content-Type: application/json' -d '{
      "jsonrpc":"2.0","id":"1","method":"message/send",
      "params":{"message":{"kind":"message","role":"user",
      "parts":[{"kind":"text","text":"Get email and address for member 378477398"}],
      "message_id":"c0ffee1234"}}}'`

- LangGraph (HTTP):
  - `curl -sS -X POST http://127.0.0.1:9004/a2a/messages -H 'Content-Type: application/json' -d '{
      "kind":"message","role":"user",
      "parts":[{"kind":"text","text":"Get email and address for member 378477398"}]}'`

Using the local A2A client
- JSON‑RPC (Strands):
  - `python notebook/misc_agents/a2a_client.py --base-url http://127.0.0.1:9003 --message "Get email and address for member 378477398"`
- HTTP (LangGraph):
  - `python notebook/misc_agents/a2a_client.py --base-url http://127.0.0.1:9004 --message "Get email and address for member 378477398"`

Expected structured output (example)
```
{"data":{"address":[
  {"name":"Address Line 1: ","value":"123 Main St"},
  {"name":"City: ","value":"Anytown"},
  {"name":"StateCd: ","value":"VA"},
  {"name":"CountryCd: ","value":"USA"},
  {"name":"ZipCd: ","value":"230123"}
],"email":[{"name":"Email: ","value":"378477398@gmail.com"}]}}
```

Security and observability
- Both agents enable CORS for local dev and attach `X-Request-ID` to responses for log correlation.
- Custom endpoints (`/healthz`, `/version`) are protected by a static API key (env var; defaults to `dev-key`).
- The Strands agent’s fast‑path middleware returns fully valid A2A JSON‑RPC to keep SDKs happy.

Source map (key code paths)
- Strands Profile Agent
  - Server + agent card + JSON‑RPC: `profile-strands-agent.py`
  - Fast‑path JSON‑RPC middleware: `profile-strands-agent.py`
  - Deterministic tool: `get_profile_overview` in `profile-strands-agent.py`

- LangGraph Profile Agent
  - Agent card (preferredTransport=HTTP): `profile-langgraph-agent.py`
  - HTTP messaging (`/a2a/messages`): `profile-langgraph-agent.py`

- Orchestrator
  - A2A Agent‑as‑a‑Tool wrapper (card caching + transport match): `orchestrator-agent.py`
  - Programmatic tool invocation (no LLM paraphrase): `orchestrator-agent.py`

Notes & next steps
- Replace the stubbed profile logic with real downstream calls (or wire `handle_request_async`).
- If you want the orchestrator to expose an HTTP API (`/query`), wrap the tool’s `invoke()` in a small FastAPI route.
- Extend the Strands fast‑path to route to additional deterministic tools by simple intent rules.

