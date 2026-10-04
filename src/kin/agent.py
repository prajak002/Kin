"""Kin conversational agent: Strands with an open-weight model, using Kin's MCP tools.

Runs locally as a chat REPL (`kin-agent`) or on Bedrock AgentCore Runtime
(`agentcore launch` with this module as the entrypoint).

Tools come from Kin's MCP server. By default it is started as a stdio
subprocess so the agent is self-contained; set KIN_MCP_URL to use a running
Streamable HTTP server instead.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from dotenv import load_dotenv
from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient

load_dotenv()

PROVIDER = os.environ.get("KIN_MODEL_PROVIDER", "ollama")
DEFAULT_MODELS = {"ollama": "llama3.2", "openai": "openai/gpt-oss-120b", "bedrock": "global.anthropic.claude-sonnet-5-5"}
MODEL_ID = os.environ.get("KIN_MODEL_ID") or DEFAULT_MODELS.get(PROVIDER, "")

SYSTEM_PROMPT = """\
You are Kin, a warm companion for an older adult who lives alone. You talk by
voice, so keep replies short, plain and spoken: one or two sentences, no lists,
no markdown, no emoji. Ask one question at a time and leave room to answer.

The person you look after has person_id "{person_id}". Today is {today}.

- Early in the first conversation of the day, ask how they are feeling. Once they
  have described it in their own words, record it with daily_checkin, turning
  what they said into a mood from 1 to 5 yourself (e.g. "a bit lonely" is 2).
  Don't ask them for a number, and don't record a mood they haven't described.
- Whenever a medication comes up, record it with log_medication right away,
  using whatever name they give (e.g. "blood pressure tablet"). If a dose was
  missed, don't tell them to take it now, double up or skip it; say their
  pharmacist or doctor can tell them what to do, and offer to let family know.
- When they want to chat about the past, or seem low or lonely, call
  start_reminiscence and bring up one film or song at a time from the result.
  Ask what it reminds them of; don't recite the list.
- If they mention a fall, chest pain, breathing trouble, confusion, or ask for
  help, call alert_family with level "urgent" straight away and tell them their
  family is being contacted. For a fall with pain, chest pain or trouble
  breathing, also ask them to call local emergency services now.
- Never give medical advice or change medication instructions. Suggest they
  check with their doctor or family instead.
- If a tool says the person is unknown, ask for their name, birth year and
  hometown, then call register_person. Include any films, artists or songs they
  mention as favourites (just the names, e.g. "Satyajit Ray"), and their mother tongue as language if they say it or
  it is clear from their hometown (for example Bengali for Kolkata).
"""


def mcp_client() -> MCPClient:
    if url := os.environ.get("KIN_MCP_URL"):
        return MCPClient(url=url)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kin.mcp_server"],
        env={**os.environ, "KIN_MCP_TRANSPORT": "stdio"},
    )
    return MCPClient(lambda: stdio_client(params))


def build_model():
    """Open-weight models by default: Ollama locally, or any OpenAI-compatible
    host (Groq, OpenRouter, vLLM) via KIN_MODEL_PROVIDER=openai."""
    if PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        # Thinking off: replies are spoken, so latency matters more than deliberation.
        return OllamaModel(
            os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=MODEL_ID,
            additional_args={"think": False},
        )
    if PROVIDER == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"base_url": os.environ["KIN_OPENAI_BASE_URL"], "api_key": os.environ["KIN_OPENAI_API_KEY"]},
            model_id=MODEL_ID,
            # gpt-oss and other reasoning models: keep deliberation short for voice.
            params={"reasoning_effort": os.environ.get("KIN_REASONING_EFFORT", "low")},
        )
    if PROVIDER == "bedrock":
        return BedrockModel(model_id=MODEL_ID, region_name=os.environ.get("AWS_REGION"))
    raise ValueError(f"Unknown KIN_MODEL_PROVIDER '{PROVIDER}' (use ollama, openai or bedrock).")


def build_agent(person_id: str, tools: MCPClient | None = None, quiet: bool = False) -> Agent:
    model = build_model()
    # quiet: no streamed output (which would include the model's reasoning).
    kwargs = {"callback_handler": None} if quiet else {}
    return Agent(
        model=model,
        tools=[tools or mcp_client()],
        system_prompt=SYSTEM_PROMPT.format(person_id=person_id, today=date.today().isoformat()),
        # Free-tier hosts cap tokens per minute; the last ~10 exchanges are enough for a chat.
        conversation_manager=SlidingWindowConversationManager(window_size=int(os.environ.get("KIN_HISTORY", "20"))),
        **kwargs,
    )


# AgentCore Runtime keeps one microVM per session, so agents are cached by session.
app = BedrockAgentCoreApp()
_sessions: dict[str, Agent] = {}


@app.entrypoint
async def invoke(payload: dict, context) -> dict:
    prompt = payload.get("prompt")
    if not prompt:
        return {"error": "payload needs a 'prompt'"}
    person_id = payload.get("person_id", "default")
    key = f"{context.session_id or 'local'}:{person_id}"
    if key not in _sessions:
        _sessions[key] = build_agent(person_id, quiet=True)
    result = await _sessions[key].invoke_async(prompt)
    return {"reply": str(result).strip()}


def chat() -> None:
    person_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KIN_PERSON_ID", "default")
    agent = build_agent(person_id, quiet=True)
    print(f"Kin ({PROVIDER}:{MODEL_ID}) talking with '{person_id}'. Ctrl-D to quit.")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line:
            print(f"kin> {str(agent(line)).strip()}")


if __name__ == "__main__":
    app.run()
