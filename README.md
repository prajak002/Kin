# Kin

A voice-first care companion for older adults who live alone. Kin does a daily
check-in, keeps track of medication, runs reminiscence conversations built from
the music and films of the person's youth, and keeps their family informed.

## Status

| Part | State |
|---|---|
| Qloo client and reminiscence engine | done, Wikidata fallback without a key |
| MCP server (Streamable HTTP or stdio) | done, JSON or DynamoDB store, ntfy alerts |
| Strands agent (open-weight models via Ollama or any OpenAI-compatible host) | done, AgentCore packaging ready |
| WhatsApp / SMS / SES family channel | planned |
| Family dashboard and simulated Alexa+ (Next.js) | planned |
| AWS CDK deployment | planned |

## Run

```sh
cp .env.example .env        # add QLOO_API_KEY
uv sync
uv run pytest
uv run kin-mcp              # http://127.0.0.1:8000/mcp
uv run scripts/qloo_probe.py
ollama pull llama3.2
uv run kin-agent asha        # chat with Kin in the terminal
```

The agent runs an open-weight model: Ollama locally by default
(`KIN_MODEL_ID`, default `llama3.2`), or any OpenAI-compatible host serving open
models with `KIN_MODEL_PROVIDER=openai`. Groq's free tier (no card needed) serving
`openai/gpt-oss-120b` answers in a few seconds; see `.env.example`. It starts the MCP server
over stdio unless `KIN_MCP_URL` is set. Deploy it to AgentCore Runtime with
the starter toolkit:

```sh
uv run agentcore configure -e src/kin/agent.py
uv run agentcore launch
uv run agentcore invoke '{"prompt": "Good morning", "person_id": "asha"}'
```

Set `KIN_TABLE` to store data in DynamoDB instead of the local JSON file
(needed on AgentCore, whose disk does not persist). Create the table once:

```sh
uv run python -c "from kin.dynamo import create_table; create_table('kin')"
```

Family alerts go to the ntfy topic in `KIN_NTFY_TOPIC`. Without a Qloo key,
reminiscence material comes from Wikidata (CC0).

## MCP tools

- `register_person`: profile, birth year, hometown, favourites, medications
- `daily_checkin`: mood 1-5 and notes; a mood of 2 or lower alerts family
- `log_medication`: taken or skipped
- `start_reminiscence`: Qloo films and artists for the person's ages 10-30, weighted by hometown and favourites
- `alert_family`: info, warning or urgent
- `wellbeing_summary`: recent mood, missed doses and alerts

## Hackathon entries

| Hackathon | What it uses here |
|---|---|
| Amazon Developer, Alexa+ track | `src/kin/mcp_server.py` |
| Qloo Agentic | `src/kin/qloo.py`, `src/kin/reminiscence.py`, `src/kin/agent.py` |

Kin is not a medical device and does not give medical advice.

## License

MIT
