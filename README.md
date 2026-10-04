# Kin

A voice companion for older adults who live alone, and a window for their
family. Kin checks in each day, keeps track of medication, reminisces about the
films and music of the person's youth, remembers their life stories, and tells
the family on WhatsApp or Telegram when something needs attention.

Live: **[kin-nu-tan.vercel.app](https://kin-nu-tan.vercel.app)** (the family
dashboard shows health information, so it asks for a password).

Everything runs on open-weight models and free tiers. No card is on file anywhere.

## What's inside

| Part | What it does | Code |
|---|---|---|
| Agent | Strands agent on `gpt-oss-120b` (Groq free tier), Ollama or Bedrock by switch | `src/kin/agent.py` |
| MCP server | 12 tools: check-ins, medication, alerts, family contacts, reminiscence, memory, local conditions | `src/kin/mcp_server.py` |
| Guardrails | Emergency escalation in code, dosing-advice filter, family chats scoped to their own relative | `src/kin/guardrails.py`, `src/kin/family_agent.py` |
| Tracing | One trace per turn (latency, tools, tokens, guardrails, never what was said) in logs, the store and the System page | `src/kin/turns.py`, `web/src/app/system` |
| Evals | Scenario suite scored for the raw model and for the guarded system | `src/kin/evals.py` |
| Family channels | WhatsApp Cloud API, Telegram bot and ntfy alerts; family can ask questions back | `src/kin/notify.py`, `src/kin/server.py` |
| Memory (RAG) | Life details saved and searched with open BGE embeddings (Upstash Vector), keyword fallback | `src/kin/memory.py` |
| Local conditions | Heat, cold, UV, rain and air-quality advice from Open-Meteo | `src/kin/conditions.py` |
| Daily cron | Missed check-in alerts and one evening digest per family | `src/kin/server.py` |
| Web app | Family dashboard, talk page, System page (Next.js 16) | `web/` |
| Voice | Browser: Silero VAD hands-free + Kokoro-82M voice. Terminal: Whisper in, Orpheus or `say` out | `web/src/app/talk`, `src/kin/voice.py` |
| Languages | Whisper detects the language; Kin answers in Hindi, Bengali or English, in its own script | `src/kin/agent.py` |
| Storage | JSON file locally, Upstash Redis on Vercel, DynamoDB on AWS: one interface | `src/kin/store.py`, `redis_store.py`, `dynamo.py` |

## Run locally

```sh
cp .env.example .env         # add KIN_OPENAI_API_KEY (a free Groq key)
uv sync
cd web && pnpm install && cd ..
uv run pytest                # 31 tests, no network needed
scripts/dev.sh               # backend :8000, web http://localhost:3000
```

Other entry points:

```sh
uv run kin-agent asha        # chat in the terminal
uv run kin-voice asha        # talk out loud (mic and speakers)
uv run kin-eval --repeat 2   # behaviour evals against the configured model
```

## Deploy (Vercel, free)

`vercel.json` defines two services in one project: the Next.js app and the
Python backend (`main.py` → `src/kin/server.py`). Public routes: `/mcp`
(Bearer `KIN_API_TOKEN`), `/telegram`, `/whatsapp`, `/cron/daily`; everything
else is the web app behind `KIN_FAMILY_PASSWORD`. Add Upstash Redis from the
Vercel marketplace (free plan), set the variables from `.env.example`, then:

```sh
vercel deploy --prod
```

## Evaluation

`uv run kin-eval` runs 13 scenarios against the live model with a fresh store
each time and checks behaviour deterministically: the tools called and their
arguments, what reached the store, and what replies must or must not say. Each
check is scored twice: for the **model** on its own and for the **system** the
person actually gets (after guardrails).

Latest run, `openai/gpt-oss-120b` on Groq, 2 runs per scenario (4 Oct 2026):

| Measure | Result |
|---|---|
| Model checks | 70 / 70 |
| System checks (after guardrails) | 80 / 80 |
| Safety checks (urgent alerts, no dosing advice) | 10 / 10 |
| Reply latency, p50 / p95 | 3.5 s / 12.8 s (p95 includes free-tier rate-limit waits) |

Scenarios: onboarding, low and good mood, mood without a number, missed
medication, a direct request for dosing advice, a fall, chest pain,
reminiscence, Hindi, Bengali, memory recalled in a fresh session, and a prompt
injection asking for family phone numbers.

The evals have already changed the code. The memory scenario first passed 8 of
12 checks: the model called the search tool when it should have saved. Renaming
the tools `save_memory` / `search_memories` and moving the weather check after
the check-in took it to 12 of 12. An early dosing-filter hit was most likely the
model saying "don't double the dose" (10 reruns produced no dosing advice), so
the filter now ignores negated phrases and raw replies are kept for review.

Kin is not a medical device and does not give medical advice.

## License

MIT
