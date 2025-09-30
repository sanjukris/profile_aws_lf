




python misc_agents/profile-strands-agent.py
python misc_agents/orchestrator-agent.py --question "show address for member 378477398"


python misc_agents/profile-langgraph-agent.py
change the port to 9004
python misc_agents/orchestrator-agent.py --question "show address for member 378477398"

How to run the agents

### Strands‑based agent

python misc_agents/profile-strands-agent.py
Agent card: curl http://127.0.0.1:9003/.well-known/agent-card.json
Protected custom endpoints (API key default dev-key):
Health: curl -H 'x-api-key: dev-key' http://127.0.0.1:9003/healthz
Version: curl -H 'x-api-key: dev-key' http://127.0.0.1:9003/version

### LangGraph‑based agent

python notebook/misc_agents/profile-langgraph-agent.py
Agent card: curl http://127.0.0.1:9004/.well-known/agent-card.json
Protected custom endpoints (API key default dev-key):
Health: curl -H 'x-api-key: dev-key' http://127.0.0.1:9004/healthz
Version: curl -H 'x-api-key: dev-key' http://127.0.0.1:9004/version

### How to use the A2A client

Send a message (non‑streaming):
python misc_agents/a2a_client.py --base-url http://127.0.0.1:9003 --message "Get email and address for member 378477398"
python misc_agents/a2a_client.py --base-url http://127.0.0.1:9004 --message "Get address for member 378477398"
Stream mode (optional):
python notebook/misc_agents/a2a_client.py --base-url http://127.0.0.1:9003 --message "..." --stream
About handle_request and handle_request_async

curl -sS -X POST http://127.0.0.1:9003/ -H 'Content-Type: application/json' -d '{
"jsonrpc":"2.0","id":"1","method":"message/send",
"params":{"message":{"kind":"message","role":"user",
"parts":[{"kind":"text","text":"Get email and address for member 378477398"}],
"message_id":"c0ffee1234"}}}'

curl -sS -X POST http://127.0.0.1:9004/ -H 'Content-Type: application/json' -d '{
"jsonrpc":"2.0","id":"1","method":"message/send",
"params":{"message":{"kind":"message","role":"user",
"parts":[{"kind":"text","text":"Get email and address for member 378477398"}],
"message_id":"c0ffee1234"}}}'


curl -sS -X POST http://127.0.0.1:9004/a2a/messages -H 'Content-Type: application/json' -d '{
"kind": "message",
"role": "user",
"parts": [{"kind": "text", "text": "Get email and address for member 378477398"}]
}'
curl -sS -X POST http://127.0.0.1:9004/query -H 'Content-Type: application/json' -d '{"question":"Get email and address for member 378477398"}'


Current state
The earlier interfaces referenced agents.profile_agent_aws.handle_request and agents.profile_agent_lg.handle_request_async. Those modules weren’t present in this repo, so I replaced the direct endpoint handling with a standards‑compliant A2AServer and a stub tool.
What I added
If those functions are available in your environment, they will now be used:
profile-strands-agent.py: Tries to import handle_request and calls it within get_profile_overview.
profile-langgraph-agent.py: Tries to import handle_request_async and calls it within get_profile_overview.
Expected return shape
If your handler already returns the final JSON you want to expose, I return it directly (as JSON string).
If you want it normalized to the NameValue schema, I can add a mapper once you confirm your handler’s payload structure.
Notes

A2A JSON‑RPC is best exercised via the provided A2A client (it builds the correct JSON‑RPC envelope for your version). For cURL, use agent-card, health, and version endpoints; JSON‑RPC payloads vary by SDK version and are easy to get wrong by hand.
If you want me to force the tool to always normalize handler outputs into the NameValue schema, point me to the handler’s payload shape and I’ll add the mapper.

--- 

s it not possible to test these A2A profile agents main functionality by passing the user queries via cURLs? If possible, give me sample cURLs and the expected responses?


Yes, you can test the A2A profile agents with curl. The only caveat: the A2A transport is JSON‑RPC 2.0, so you must send a proper JSON‑RPC envelope. Below are ready‑to‑use curls for both agents and what you should expect back.

Agent discovery

Strands agent (port 9003)
curl http://127.0.0.1:9003/.well-known/agent-card.json
LangGraph agent (port 9004)
curl http://127.0.0.1:9004/.well-known/agent-card.json
Health/version (API key protected)

Strands: curl -H 'x-api-key: dev-key' http://127.0.0.1:9003/healthz
LangGraph: curl -H 'x-api-key: dev-key' http://127.0.0.1:9004/healthz
Send a user query via JSON‑RPC
Most A2A servers accept method "message.send". If your server returns “Method not found,” try "message/send". These bodies send: “Get email and address for member 378477398”.

Strands agent (9003)

curl -X POST http://127.0.0.1:9003/ -H 'Content-Type: application/json' -d '{
"jsonrpc": "2.0",
"id": "1",
"method": "message.send",
"params": {
"message": {
"kind": "message",
"role": "user",
"parts": [{"kind": "text", "text": "Get email and address for member 378477398"}],
"message_id": "c0ffee1234"
}
}
}'
LangGraph agent (9004)

curl -X POST http://127.0.0.1:9004/ -H 'Content-Type: application/json' -d '{
"jsonrpc": "2.0",
"id": "1",
"method": "message.send",
"params": {
"message": {
"kind": "message",
"role": "user",
"parts": [{"kind": "text", "text": "Get email and address for member 378477398"}],
"message_id": "c0ffee1234"
}
}
}'
Expected responses

You will receive a JSON‑RPC 2.0 response with result that is either:
A final Message:
Example: {"jsonrpc":"2.0","id":"1","result":{"kind":"message","role":"assistant","parts":[{"kind":"text","text":"{"data":{"address":[{"name":"Address Line 1: ","value":"123 Main St"},{"name":"City: ","value":"Anytown"},{"name":"StateCd: ","value":"VA"},{"name":"CountryCd: ","value":"USA"},{"name":"ZipCd: ","value":"230123"}],"email":[{"name":"Email: ","value":"378477398@gmail.com"}]}}"}]}}
Or a completed Task (common when servers are configured for streaming internally):
Example (trimmed):
{
"jsonrpc": "2.0",
"id": "1",
"result": {
"kind": "task",
"status": {"state": "completed"},
"artifacts": [
{
"name": "agent_response",
"parts": [
{
"kind": "text",
"text": "{"data":{"address":[{"name":"Address Line 1: ","value":"123 Main St"},{"name":"City: ","value":"Anytown"},{"name":"StateCd: ","value":"VA"},{"name":"CountryCd: ","value":"USA"},{"name":"ZipCd: ","value":"230123"}],"email":[{"name":"Email: ","value":"378477398@gmail.com"}]}}"
}
]
}
]
}
}
Notes

The two agents return the same structured JSON for this query; the LangGraph agent includes the same tool stub unless you’ve wired in your LangGraph flow (then it will call that).
If a method error occurs:
Try "message/send" instead of "message.send".
Or use the included A2A client to avoid JSON‑RPC guesswork:
python notebook/misc_agents/a2a_client.py --base-url http://127.0.0.1:9003 --message "Get email and address for member 378477398"
Custom endpoints like /healthz and /version require API key (default dev-key) but A2A endpoints do not.
Want me to add a tiny script that prints just the final JSON from either Message or Task for cURL output? I can add a jq example or a helper script if useful.