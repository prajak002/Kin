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
from strands import Agent, ModelRetryStrategy
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from strands.tools.tools import PythonAgentTool
from strands.types.tools import ToolResult, ToolUse

load_dotenv()

PROVIDER = os.environ.get("KIN_MODEL_PROVIDER", "ollama")
DEFAULT_MODELS = {"ollama": "llama3.2", "openai": "openai/gpt-oss-120b", "bedrock": "global.anthropic.claude-sonnet-5-5"}
MODEL_ID = os.environ.get("KIN_MODEL_ID") or DEFAULT_MODELS.get(PROVIDER, "")

SYSTEM_PROMPT = """\
You are Kin, a warm companion for an older adult who lives alone. You talk by
voice, so keep replies short, plain and spoken: one or two sentences, no lists,
no markdown, no emoji. Ask one question at a time and leave room to answer.
Reply in the language they speak to you in (English, Hindi, Bengali and so on),
in its own script. Tool arguments and notes stay in English.

The person you look after has person_id "{person_id}". Today is {today}.

- Early in the first conversation of the day, ask how they are feeling. Once they
  have described it in their own words, record it with daily_checkin, turning
  what they said into a mood from 1 to 5 yourself (e.g. "a bit lonely" is 2).
  Don't ask them for a number, and don't record a mood they haven't described.
- Whenever a medication comes up, record it with log_medication right away,
  using whatever name they give (e.g. "blood pressure tablet"). If a dose was
  missed, don't tell them to take it now, double up or skip it; say their
  pharmacist or doctor can tell them what to do, and offer to let family know.
- Once they've told you how they are, call local_conditions once a day and, if
  it has advice (heat, cold, poor air), mention one point gently, like a friend.
- Always respond to what they just said before bringing up anything else.
- When they tell you about a person by name, a place they lived or an event,
  call save_memory straight away, without asking permission.
- When they ask "do you remember…" or bring someone up again, call
  search_memories first and answer from what it returns.
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
        from strands.models.routing import ModelRouter

        def openai_model(model_id: str) -> OpenAIModel:
            return OpenAIModel(
                client_args={"base_url": os.environ["KIN_OPENAI_BASE_URL"], "api_key": os.environ["KIN_OPENAI_API_KEY"]},
                model_id=model_id,
                # gpt-oss and other reasoning models: keep deliberation short for voice.
                params={"reasoning_effort": os.environ.get("KIN_REASONING_EFFORT", "low")},
            )

        # Free tiers cap tokens per model per day; a second open model with its own
        # quota keeps Kin answering when the first is throttled.
        fallback = os.environ.get("KIN_FALLBACK_MODEL_ID", "openai/gpt-oss-20b")
        if not fallback or fallback == MODEL_ID:
            return openai_model(MODEL_ID)
        return ModelRouter([openai_model(MODEL_ID), openai_model(fallback)])
    if PROVIDER == "bedrock":
        return BedrockModel(model_id=MODEL_ID, region_name=os.environ.get("AWS_REGION"))
    raise ValueError(f"Unknown KIN_MODEL_PROVIDER '{PROVIDER}' (use ollama, openai or bedrock).")


async def inprocess_tools() -> list[PythonAgentTool]:
    """Kin's MCP tools called directly in this process: same schemas and checks as
    over MCP, without a server round trip. Used where a subprocess per request
    would be too slow (serverless)."""
    from .mcp_server import server

    def wrap(name: str):
        async def run(tool_use: ToolUse, **_: object) -> ToolResult:
            try:
                result = await server.call_tool(name, tool_use["input"])
                text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
                status = "error" if result.is_error else "success"
            except Exception as e:  # ToolError and validation errors reach the model
                text, status = str(e), "error"
            return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"text": text}]}

        return run

    return [
        PythonAgentTool(t.name, {"name": t.name, "description": t.description or "", "inputSchema": {"json": t.input_schema}}, wrap(t.name))
        for t in await server.list_tools()
    ]


def build_agent(
    person_id: str,
    tools: MCPClient | list | None = None,
    quiet: bool = False,
    messages: list | None = None,
) -> Agent:
    model = build_model()
    # quiet: no streamed output (which would include the model's reasoning).
    kwargs = {"callback_handler": None} if quiet else {}
    return Agent(
        model=model,
        # Fail over within seconds instead of backing off for minutes on a throttled model.
        retry_strategy=ModelRetryStrategy(max_attempts=2, initial_delay=1, max_delay=2),
        tools=tools if isinstance(tools, list) else [tools or mcp_client()],
        messages=messages,
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
