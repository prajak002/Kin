# Kin

A voice-first care companion for older adults who live alone. Kin does a daily
check-in, keeps track of medication, runs reminiscence conversations built from
the music and films of the person's youth, and keeps their family informed.

## Status

| Part | State |
|---|---|
| Qloo client and reminiscence engine | done |
| MCP server (Streamable HTTP) | done, local JSON store |
| Strands agent on Bedrock AgentCore | next |
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
```

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
| AWS CDS Agentic AI | WhatsApp, SMS and SES channels (planned) |
| Qloo Agentic | `src/kin/qloo.py`, `src/kin/reminiscence.py` |

Kin is not a medical device and does not give medical advice.

## License

MIT
