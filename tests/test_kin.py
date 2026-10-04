import json

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from kin.qloo import MOVIE, QlooClient, age_bucket
from kin.reminiscence import Person, build_with_qloo
from kin.store import Store


def fake_qloo(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/search":
            q = request.url.params["query"]
            return httpx.Response(200, json={"results": [{"entity_id": f"id-{q}", "name": q, "types": [MOVIE]}]})
        kind = request.url.params["filter.type"]
        entities = [
            {"entity_id": "e1", "name": f"{kind}-hit", "types": [kind],
             "properties": {"release_year": 1965}, "query": {"affinity": 0.9}},
        ]
        return httpx.Response(200, json={"success": True, "results": {"entities": entities}})

    return httpx.MockTransport(handler)


def test_age_bucket():
    assert age_bucket(30) == "35_and_younger"
    assert age_bucket(45) == "36_to_55"
    assert age_bucket(78) == "55_and_older"


async def test_build_set_uses_formative_years_and_seeds():
    calls: list[httpx.Request] = []
    async with QlooClient("k", "https://qloo.test", transport=fake_qloo(calls)) as qloo:
        result = await build_with_qloo(qloo, Person("Asha", 1948, "Kolkata", ["Satyajit Ray"]))

    movie_call = next(c for c in calls if c.url.params.get("filter.type") == MOVIE)
    assert movie_call.headers["X-Api-Key"] == "k"
    assert movie_call.url.params["filter.release_year.min"] == "1958"
    assert movie_call.url.params["filter.release_year.max"] == "1978"
    assert movie_call.url.params["signal.interests.entities"] == "id-Satyajit Ray"
    assert movie_call.url.params["signal.location.query"] == "Kolkata"

    data = result
    assert data["formative_years"] == "1958-1978"
    assert data["films"][0] == {"name": f"{MOVIE}-hit", "year": 1965, "affinity": 0.9}


async def test_search_404_is_empty():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    async with QlooClient("k", "https://qloo.test", transport=transport) as qloo:
        assert await qloo.search("nothing") == []


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "s.json")
    store.upsert_person("asha", name="Asha")
    store.add_checkin("asha", 2, "tired")
    store.add_dose("asha", "metformin", False)
    assert store.recent("checkins", "asha")[0]["mood"] == 2
    assert json.loads((tmp_path / "s.json").read_text())["doses"][0]["taken"] is False


async def test_mcp_low_mood_raises_alert(tmp_path, monkeypatch):
    from kin import mcp_server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    out = mcp_server.daily_checkin("asha", 2, "slept badly")
    assert out["alert"]["level"] == "warning"
    assert mcp_server.wellbeing_summary("asha")["average_mood"] == 2
    assert [p["name"] for p in mcp_server.list_people()] == ["Asha"]

    with pytest.raises(ToolError, match="register_person"):
        mcp_server.daily_checkin("nobody", 4)


def test_agent_reaches_kin_tools_over_stdio(tmp_path, monkeypatch):
    from kin.agent import mcp_client

    monkeypatch.setenv("KIN_STORE", str(tmp_path / "s.json"))
    monkeypatch.delenv("KIN_MCP_URL", raising=False)
    with mcp_client() as client:
        names = {t.tool_name for t in client.list_tools_sync()}
        assert {"daily_checkin", "start_reminiscence", "alert_family"} <= names
        client.call_tool_sync("t1", "register_person", {"person_id": "asha", "name": "Asha",
                                                        "birth_year": 1948, "hometown": "Kolkata"})
        out = client.call_tool_sync("t2", "daily_checkin", {"person_id": "asha", "mood": 4})
    assert out["status"] == "success"
    assert json.loads((tmp_path / "s.json").read_text())["checkins"][0]["mood"] == 4


def test_dynamo_store_matches_json_store(monkeypatch):
    from moto import mock_aws

    from kin.dynamo import DynamoStore, create_table

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        create_table("kin-test")
        store = DynamoStore("kin-test")
        store.upsert_person("asha", name="Asha", birth_year=1948)
        store.upsert_person("asha", hometown="Kolkata")
        assert store.get_person("asha") == {"id": "asha", "medications": [], "family": [],
                                            "name": "Asha", "birth_year": 1948, "hometown": "Kolkata"}
        assert store.get_person("nobody") is None

        store.add_checkin("asha", 3, at="2020-01-01T08:00:00+00:00")
        store.add_checkin("asha", 2, "tired")
        store.add_dose("asha", "metformin", False)
        store.add_moment("asha", "reminiscence", ["Pather Panchali"])
        assert [c["mood"] for c in store.recent("checkins", "asha")] == [3, 2]
        assert [c["mood"] for c in store.recent("checkins", "asha", limit=1)] == [2]
        assert [c["mood"] for c in store.today("checkins", "asha")] == [2]
        assert store.recent("doses", "asha")[0]["taken"] is False
        assert store.recent("moments", "asha")[0]["items"] == ["Pather Panchali"]
        assert [p["id"] for p in store.list_people()] == ["asha"]


def test_alerts_reach_every_family_channel(tmp_path, monkeypatch):
    from kin import mcp_server

    sent = []

    def fake_post(url, **kw):
        sent.append((url, kw.get("json"), kw.get("headers", {})))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.setenv("KIN_TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("KIN_WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("KIN_WHATSAPP_PHONE_ID", "123")
    monkeypatch.setenv("KIN_WHATSAPP_TEMPLATE", "kin_alert")
    monkeypatch.delenv("KIN_NTFY_TOPIC", raising=False)

    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.add_family_contact("asha", "Ravi", "whatsapp", "+91 98765 43210", "son")
    mcp_server.add_family_contact("asha", "Mira", "telegram", "555")
    mcp_server.add_family_contact("asha", "Mira", "telegram", "555", "daughter")  # update, not duplicate
    alert = mcp_server.alert_family("asha", "Fell in the bathroom", "urgent")

    assert [r["ok"] for r in alert["notified"]] == [True, True]
    wa = next(s for s in sent if "graph.facebook.com" in s[0])
    assert wa[0].endswith("/123/messages") and wa[1]["to"] == "919876543210"
    assert wa[1]["template"]["components"][0]["parameters"][1]["text"] == "Fell in the bathroom"
    tg = next(s for s in sent if "api.telegram.org/bottg-token" in s[0])
    assert tg[1]["chat_id"] == "555" and "Urgent" in tg[1]["text"]

    monkeypatch.delenv("KIN_TELEGRAM_BOT_TOKEN")
    failed = mcp_server.alert_family("asha", "test", "info")["notified"]
    assert [r["ok"] for r in failed] == [True, False]

    mcp_server.remove_family_contact("asha", "telegram", "555")
    assert [c["name"] for c in mcp_server.list_people()[0]["family"]] == ["Ravi"]


def fake_upstash() -> httpx.MockTransport:
    """Just enough of Upstash's REST API for RedisStore."""
    kv: dict = {}

    def run(cmd):
        op, key, *rest = cmd
        if op == "SET":
            kv[key] = rest[0]
            return "OK"
        if op == "GET":
            return kv.get(key)
        if op == "MGET":
            return [kv.get(k) for k in [key, *rest]]
        if op == "SADD":
            kv.setdefault(key, set()).update(rest)
            return 1
        if op == "SMEMBERS":
            return list(kv.get(key, set()))
        if op == "RPUSH":
            kv.setdefault(key, []).extend(rest)
            return len(kv[key])
        if op == "LRANGE":
            rows, start, stop = kv.get(key, []), int(rest[0]), int(rest[1])
            start = max(len(rows) + start, 0) if start < 0 else start
            stop = len(rows) + stop if stop < 0 else stop
            return rows[start:stop + 1]
        raise AssertionError(op)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/pipeline":
            return httpx.Response(200, json=[{"result": run(c)} for c in body])
        return httpx.Response(200, json={"result": run(body)})

    return httpx.MockTransport(handler)


def test_redis_store_matches_json_store():
    from kin.redis_store import RedisStore

    store = RedisStore("https://redis.test", "t", transport=fake_upstash())
    store.upsert_person("asha", name="Asha", birth_year=1948)
    store.upsert_person("asha", hometown="Kolkata")
    assert store.get_person("asha")["hometown"] == "Kolkata"
    assert store.get_person("nobody") is None
    store.add_checkin("asha", 3, at="2020-01-01T08:00:00+00:00")
    store.add_checkin("asha", 2, "tired")
    assert [c["mood"] for c in store.recent("checkins", "asha")] == [3, 2]
    assert [c["mood"] for c in store.recent("checkins", "asha", limit=1)] == [2]
    assert [c["mood"] for c in store.today("checkins", "asha")] == [2]
    assert [p["id"] for p in store.list_people()] == ["asha"]
    store.save_chat("s1", [{"role": "user", "content": [{"text": "hi"}]}])
    assert store.load_chat("s1")[0]["role"] == "user" and store.load_chat("s2") == []


def test_server_mcp_and_telegram(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.delenv("KIN_API_TOKEN", raising=False)
    monkeypatch.setenv("KIN_TELEGRAM_WEBHOOK_SECRET", "sekret")
    replies = []

    async def fake_reply(chat_id, text):
        replies.append((chat_id, text))

    monkeypatch.setattr(server, "_telegram_reply", fake_reply)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.daily_checkin("asha", 2, "a bit lonely")

    with TestClient(server.create_app()) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.post("/invocations", json={"prompt": " "}).status_code == 400

        rpc = client.post(
            "/mcp",
            headers={"accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "wellbeing_summary", "arguments": {"person_id": "asha"}}},
        )
        assert rpc.status_code == 200, rpc.text
        assert '"average_mood": 2' in rpc.json()["result"]["content"][0]["text"]

        monkeypatch.setenv("KIN_API_TOKEN", "tok")
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        accept = {"accept": "application/json, text/event-stream"}
        assert client.post("/mcp", headers=accept, json=call).status_code == 401
        assert client.post("/mcp", headers={**accept, "authorization": "Bearer tok"}, json=call).status_code == 200
        assert client.post("/mcp?key=tok", headers=accept, json=call).status_code == 200
        assert client.get("/health").status_code == 200
        monkeypatch.delenv("KIN_API_TOKEN")

        def update(text):
            return {"message": {"text": text, "chat": {"id": 42}, "from": {"first_name": "Ravi"}}}

        assert client.post("/telegram", json=update("/start asha")).status_code == 401
        hdr = {"X-Telegram-Bot-Api-Secret-Token": "sekret"}
        client.post("/telegram", headers=hdr, json=update("/start asha"))
        assert "connected to Asha" in replies[-1][1]
        assert mcp_server.list_people()[0]["family"][0] == {
            "name": "Ravi", "relation": "", "channel": "telegram", "address": "42"}
        asked = []

        async def fake_answer(channel, address, sender, text):
            asked.append((channel, address, sender, text))
            return "She's had a quiet day."

        monkeypatch.setattr(server.family_agent, "answer", fake_answer)
        client.post("/telegram", headers=hdr, json=update("how is mum?"))
        assert asked == [("telegram", "42", "Ravi", "how is mum?")]
        assert replies[-1] == (42, "She's had a quiet day.")


@pytest.mark.parametrize("text, hit", [
    ("I fell in the bathroom", True), ("I have chest pain", True), ("I can't breathe properly", True),
    ("Help me please", True), ("I watched a film about a fall of an empire", False), ("I feel fine", False),
])
def test_emergency_detection(text, hit):
    from kin.guardrails import emergency_in

    assert (emergency_in(text) is not None) == hit


@pytest.mark.parametrize("reply, hit", [
    ("Maybe you could take it now.", True), ("You can double up tonight.", True),
    ("Just skip the dose today.", True), ("Take 500 mg after food.", True), ("Stop taking it for now.", True),
    ("Your pharmacist or doctor can tell you what to do.", False), ("Did you take your tablet this morning?", False),
    ("Please don't double the dose.", False), ("It's best not to take two tablets tonight.", False),
    ("You should never double up on blood pressure pills.", False),
])
def test_dosing_advice_detection(reply, hit):
    from kin.guardrails import dosing_advice_in

    assert (dosing_advice_in(reply) is not None) == hit


class StubAgent:
    """Just what run_turn needs: invoke_async, messages and usage metrics."""

    def __init__(self, reply, tool_calls=()):
        from types import SimpleNamespace

        self.reply, self.tool_calls, self.messages = reply, tool_calls, []
        self.event_loop_metrics = SimpleNamespace(agent_invocations=[SimpleNamespace(usage={"inputTokens": 10, "outputTokens": 5})])

    async def invoke_async(self, text):
        for i, (name, args) in enumerate(self.tool_calls):
            self.messages.append({"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{i}", "name": name, "input": args}}]})
            self.messages.append({"role": "user", "content": [{"toolResult": {"toolUseId": f"t{i}", "status": "success", "content": []}}]})
        return self.reply


async def test_run_turn_traces_and_guardrails(tmp_path, monkeypatch):
    from kin import mcp_server
    from kin.turns import run_turn

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    alerts = []

    # Dosing advice is replaced; the trace records tools and tokens but no text.
    turn = await run_turn(StubAgent("Maybe take it now.", [("log_medication", {"taken": False})]),
                          "I forgot my tablet", person_id="asha", raise_alert=lambda *a: alerts.append(a))
    assert "pharmacist or doctor" in turn.reply
    assert turn.trace["tools"] == [{"name": "log_medication", "ok": True}]
    assert turn.trace["tokens"] == {"input": 10, "output": 5}
    assert turn.trace["guardrails"][0]["rule"] == "dosing_advice"
    assert "forgot" not in json.dumps(turn.trace)

    # Emergency the model ignored: the guardrail raises the alert.
    turn = await run_turn(StubAgent("Oh dear."), "I fell and can't get up", person_id="asha",
                          raise_alert=lambda *a: alerts.append(a))
    assert alerts and alerts[0][0] == "asha" and "family know" in turn.reply

    # Emergency the model handled: no second alert.
    await run_turn(StubAgent("Calling them.", [("alert_family", {"level": "urgent"})]), "I fell",
                   person_id="asha", raise_alert=lambda *a: alerts.append(a))
    assert len(alerts) == 1
    assert len(mcp_server.store.recent_traces()) == 3


def test_whatsapp_webhook(tmp_path, monkeypatch):
    import hashlib
    import hmac as hmac_

    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.setenv("KIN_WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.setenv("KIN_WHATSAPP_APP_SECRET", "app-secret")
    sent, asked = [], []

    async def fake_answer(channel, address, sender, text):
        asked.append((channel, address, sender, text))
        return "Mum checked in happy this morning."

    monkeypatch.setattr(server.family_agent, "answer", fake_answer)
    monkeypatch.setattr(server, "send_whatsapp_text", lambda to, text: sent.append((to, text)))

    payload = json.dumps({"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "919876543210", "profile": {"name": "Ravi"}}],
        "messages": [{"from": "919876543210", "type": "text", "text": {"body": "How is mum?"}}],
    }}]}]}).encode()
    sig = "sha256=" + hmac_.new(b"app-secret", payload, hashlib.sha256).hexdigest()

    with TestClient(server.create_app()) as client:
        ok = client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "123"})
        assert ok.text == "123"
        assert client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope"}).status_code == 403
        assert client.post("/whatsapp", content=payload, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
        assert client.post("/whatsapp", content=payload, headers={"X-Hub-Signature-256": sig}).status_code == 200

    assert asked == [("whatsapp", "919876543210", "Ravi", "How is mum?")]
    assert sent == [("919876543210", "Mum checked in happy this morning.")]


def test_family_agent_is_scoped_to_linked_people(tmp_path, monkeypatch):
    from kin import family_agent, mcp_server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.register_person("bina", "Bina", 1950, "Pune")
    mcp_server.add_family_contact("asha", "Ravi", "whatsapp", "+91 98765 43210")

    people = family_agent.contacts_for(mcp_server.store, "whatsapp", "919876543210")
    assert [p["id"] for p in people] == ["asha"]
    assert family_agent.contacts_for(mcp_server.store, "whatsapp", "910000000000") == []

    wellbeing, profile = family_agent._scoped_tools(people)
    assert wellbeing._tool_func("asha")["name"] == "Asha"
    with pytest.raises(ValueError, match="Not allowed"):
        wellbeing._tool_func("bina")
    with pytest.raises(ValueError, match="Not allowed"):
        profile._tool_func("bina")


def test_daily_cron_digest(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.setenv("CRON_SECRET", "cron")
    sent = []
    monkeypatch.setattr(server, "notify_family",
                        lambda person, level, text: sent.append((person["id"], level, text)) or [{"ok": True}])

    async def fake_conditions(place):
        return {"advice": [{"risk": "heat", "say": "It will feel like 39°C."}]}

    monkeypatch.setattr(server.conditions, "local_conditions", fake_conditions)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.register_person("bina", "Bina", 1950, "Pune")
    mcp_server.store.add_checkin("bina", 4, "cheerful")

    with TestClient(server.create_app()) as client:
        assert client.get("/cron/daily").status_code == 401
        out = client.get("/cron/daily", headers={"authorization": "Bearer cron"}).json()

    assert {p["person"]: p["level"] for p in out["people"]} == {"asha": "warning", "bina": "info"}
    asha = next(t for pid, _, t in sent if pid == "asha")
    assert "No check-in today" in asha and "39°C" in asha
    assert mcp_server.store.recent("alerts", "asha")[-1]["reason"] == "No check-in from Asha today."


def test_memory_store_and_vector(tmp_path, monkeypatch):
    from kin import mcp_server
    from kin.memory import VectorMemory

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.delenv("UPSTASH_VECTOR_REST_URL", raising=False)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.save_memory("asha", "Her husband Arun was a schoolteacher in Shillong.")
    mcp_server.save_memory("asha", "She sang Rabindra Sangeet at her sister's wedding.")
    hits = mcp_server.search_memories("asha", "Tell me about Arun")
    assert hits[0]["fact"].startswith("Her husband Arun")

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/query-data":
            return httpx.Response(200, json={"result": [{"score": 0.91, "metadata": {"person_id": "asha", "fact": "x"}}]})
        return httpx.Response(200, json={"result": "Success"})

    vm = VectorMemory("https://vector.test", "t", transport=httpx.MockTransport(handler))
    vm.remember("asha", "Lived in Shillong")
    assert vm.recall("asha", "hills")[0]["score"] == 0.91
    assert calls[0][1][0]["data"] == "Lived in Shillong"
    assert calls[1][1]["filter"] == "person_id = 'asha'"
