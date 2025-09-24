# profile_aws
python run_demo.py
python -m uvicorn --app-dir /path/to/profile_aws app.server.main:app --port 9000

curl -s http://127.0.0.1:9000/healthz

curl -s http://127.0.0.1:9000/a2a/agent-card | jq

python app/client/a2a_client.py


