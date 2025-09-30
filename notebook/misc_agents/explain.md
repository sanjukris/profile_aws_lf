Why it’s faster now

LLM bypass for deterministic paths:
Strands profile agent: we added a fast JSON-RPC middleware that intercepts message/send and directly calls the deterministic tool, returning a valid A2A Message. No LLM planning loop is invoked.
LangGraph profile agent: uses a plain HTTP /a2a/messages endpoint and returns the structured JSON immediately.
Transport-matching orchestration:
The orchestrator detects the remote agent’s transport (JSONRPC or HTTP) and uses the matching, minimal path instead of generic, slower flows.
No repeated discovery:
The orchestrator now caches the agent card in an A2A “agent-as-a-tool” wrapper and reuses it, avoiding redundant /.well-known/agent-card.json fetches.
Updates: Orchestrator as an “A2A Agent Tool”

What’s new in orchestrator

Wraps the remote Profile agent as a Strands tool (A2AAgentTool) with pre-resolved agent card.
Supports JSON-RPC and HTTP transports.
Returns the remote agent’s raw structured output so your final print is the exact JSON string (or JSON part) produced by the Profile agent.
Strong system prompt instructs the orchestrator agent to call the tool and return only its output (no rephrasing), preserving structure and compliance.
How it works

On startup, the tool resolves and caches the Profile agent card once.
When invoked:
preferredTransport=HTTP → POST /a2a/messages and extract the json/text parts.
preferredTransport=JSONRPC → use A2A ClientFactory and read the first final Message or Task artifact, extracting the final text/json consistently.
The orchestrator agent has exactly one tool (‘call_profile_agent’) and a prompt that says “always call it and return only its output,” ensuring fast and structured results.
How to run

Strands (JSON-RPC):
python notebook/misc_agents/profile-strands-agent.py
python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"
LangGraph (HTTP):
python notebook/misc_agents/profile-langgraph-agent.py
export PROFILE_AGENT_URL=http://127.0.0.1:9004/
python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"
What I changed

notebook/misc_agents/orchestrator-agent.py
Added A2AAgentTool class:
Pre-resolves agent card; caches preferred transport.
call_profile_agent tool chooses JSON-RPC or HTTP path accordingly and extracts structured text/json.
Rebuilt orchestrator Agent to use a single tool and a strong system prompt to return only the tool’s raw output.
Keeps Anthropic as the model, but the LLM only routes to the tool and returns its output unchanged, so cost and latency stay low.
Result

Both the Strands and LangGraph profile agents return quickly and consistently with a structured JSON payload.
The orchestrator complies with A2A (respects agent card, transport, and message semantics), avoids discovery overhead, and returns the remote agent’s structured response as-is.



--- 

Here’s what’s going on and what I fixed to make outputs consistent, structured, and fast.

Why both agents run fast

Strands Profile Agent: We added a JSON-RPC fast-path middleware that intercepts message/send and directly calls the deterministic tool returning an A2A-compliant Message. No LLM planning loop.
LangGraph Profile Agent: It exposes HTTP /a2a/messages that returns the structured JSON immediately. No LLM step.
Orchestrator: Now caches the remote agent card (A2A Agent as a Tool) and matches the transport (JSON-RPC vs HTTP) so it uses the minimal call path every time.
Why your outputs differed

When the Orchestrator wrapped the A2A agent as a tool and then ran through a Strands Agent, the LLM could occasionally add or duplicate text, leading to prose and repeats.
When using HTTP for LangGraph, we were printing just the tool outputs, but the orchestration path still went through the Agent in earlier iterations, which could lead to empty output if the LLM chose not to echo.
What I changed

Orchestrator switches to “A2A Agent as a Tool” with card caching AND calls the tool directly (no LLM in the middle):
Adds A2AAgentTool with:
Card resolution once on init (avoids repeated discovery).
preferredTransport detection and support for both JSON-RPC and HTTP.
Robust extraction of either text or json parts from responses.
Adds a programmatic method invoke(message) and keeps a @tool call_profile_agent for future Agent use.
main() now uses the tool programmatically to guarantee the exact structured output from the downstream agent.
Files updated:
notebook/misc_agents/orchestrator-agent.py: A2AAgentTool class, card caching, invoke(), transport-aware calling; main() calls tool.invoke() and prints raw structured output.
How the flow works now

Orchestrator tool resolves the card once and caches it.
For LangGraph agent (preferredTransport=HTTP):
POST base_url/a2a/messages with the question → return the json/text parts (raw structured JSON).
For Strands agent (preferredTransport=JSONRPC):
Resolve card and create a JSON-RPC client.
Send a Message and extract the final text (or json) from Message/Task events, preserving the structured JSON string.
What to run

Strands:
python notebook/misc_agents/profile-strands-agent.py
python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"
LangGraph:
python notebook/misc_agents/profile-langgraph-agent.py
export PROFILE_AGENT_URL=http://127.0.0.1:9004/
python notebook/misc_agents/orchestrator-agent.py --question "show address for member 378477398"
Expected

Both paths print only the structured JSON (the same schema and values) with no duplication or prose.
Why this is a proper solution

A2A compliance: we respect the agent card and use the correct transport for each target.
Efficiency: no repeated discovery; minimal client (HTTP or JSON-RPC) calls; no LLM rephrasing post-tool-call.
Deterministic output: exact structured JSON from the downstream agent is returned and printed.
If you want the Orchestrator to also expose an HTTP API (/query) with the same logic, I can add a FastAPI wrapper that calls tool.invoke() and returns JSON.