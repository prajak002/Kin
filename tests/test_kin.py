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

    wellbeing, profile, _ = family_agent._scoped_tools(people)
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
        if request.url.path == "/query":
            return httpx.Response(200, json={"result": [{"score": 0.91, "metadata": {"person_id": "asha", "fact": "x"}}]})
        return httpx.Response(200, json={"result": "Success"})

    vm = VectorMemory("https://vector.test", "t", transport=httpx.MockTransport(handler), embedder=lambda t: [0.1, 0.2])
    vm.remember("asha", "Lived in Shillong")
    assert vm.recall("asha", "hills")[0]["score"] == 0.91
    assert calls[0][1][0]["vector"] == [0.1, 0.2] and calls[0][1][0]["metadata"]["fact"] == "Lived in Shillong"
    assert calls[1][1]["filter"] == "person_id = 'asha'"


def test_register_person_partial_update(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError as TE

    from kin import mcp_server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    with pytest.raises(TE, match="needs name"):
        mcp_server.register_person("asha", name="Asha")
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    p = mcp_server.register_person("asha", favourites=["Satyajit Ray"])
    assert (p["birth_year"], p["hometown"], p["lives_in"], p["favourites"]) == (1948, "Kolkata", "Kolkata", ["Satyajit Ray"])


def test_failed_model_call_still_escalates(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.delenv("KIN_API_TOKEN", raising=False)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")

    async def broken(*a, **k):
        raise RuntimeError("model API error")

    async def no_tools():
        return []

    monkeypatch.setattr(server, "run_turn", broken)
    monkeypatch.setattr(server, "inprocess_tools", no_tools)
    monkeypatch.setattr(server, "notify_family", lambda *a: [])
    with TestClient(server.create_app()) as client:
        ok = client.post("/invocations", json={"prompt": "Tell me a story", "person_id": "asha"})
        assert ok.status_code == 200 and "say it again" in ok.json()["reply"]
        fall = client.post("/invocations", json={"prompt": "I fell and can't get up", "person_id": "asha"})
        assert "family know" in fall.json()["reply"]
    assert mcp_server.store.recent("alerts", "asha")[-1]["level"] == "urgent"


async def test_run_turn_reports_actions(tmp_path, monkeypatch):
    from kin import mcp_server
    from kin.turns import run_turn

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    calls = [("daily_checkin", {"mood": 2}), ("log_medication", {"medication": "Metformin", "taken": True}),
             ("alert_family", {"level": "warning", "reason": "Possible scam: a caller asked for her OTP"})]
    turn = await run_turn(StubAgent("Thank you for telling me.", calls), "hi", person_id="asha")
    assert [a["kind"] for a in turn.actions] == ["checkin", "medication", "scam"]
    assert turn.actions[1]["text"] == "Logged Metformin: taken"
    assert "Metformin" not in json.dumps(turn.trace)  # actions name things; traces don't

    turn = await run_turn(StubAgent("Oh dear."), "I fell down", person_id="asha", raise_alert=lambda *a: None)
    assert turn.actions[-1]["kind"] == "alert"


@pytest.mark.parametrize("text,hit", [
    ("A man from the bank called and asked for my OTP", True),
    ("They said my account will be blocked unless I update KYC", True),
    ("বলল আমার ওটিপি লাগবে", True),
    ("बैंक वाले बोले खाता बंद हो जाएगा", True),
    ("A CBI officer said I'm under digital arrest", True),
    ("I pinned Ravi's photo to the fridge", False),
    ("My blood pressure tablet is in the kitchen", False),
])
def test_scam_detection(text, hit):
    from kin.guardrails import scam_in

    assert (scam_in(text) is not None) == hit


async def test_scam_guardrail_alerts_family_once(tmp_path, monkeypatch):
    from kin import mcp_server
    from kin.turns import run_turn

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    alerts = []
    turn = await run_turn(StubAgent("Don't share it."), "Someone wants my OTP", person_id="asha",
                          raise_alert=lambda *a: alerts.append(a))
    assert alerts[0][2] == "warning" and alerts[0][1].startswith("Possible scam")
    assert turn.actions[-1]["kind"] == "scam"

    # The model warned the family itself: no second alert.
    await run_turn(StubAgent("Hang up.", [("alert_family", {"level": "warning", "reason": "Possible scam: OTP"})]),
                   "Someone wants my OTP", person_id="asha", raise_alert=lambda *a: alerts.append(a))
    assert len(alerts) == 1


def test_medication_reminders(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from kin import mcp_server, reminders

    store = Store(tmp_path / "s.json")
    monkeypatch.setattr(mcp_server, "store", store)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    person = mcp_server.set_medication_schedule("asha", "Metformin", ["8:00", "20:00"])
    assert person["schedule"] == [{"medication": "Metformin", "times": ["08:00", "20:00"]}]
    assert "Metformin" in person["medications"]
    with pytest.raises(ToolError, match="isn't a time"):
        mcp_server.set_medication_schedule("asha", "Metformin", ["8am"])

    alerts = []
    fake_alert = lambda p, level, reason: alerts.append((level, reason))  # noqa: E731
    ist = lambda h, m: datetime(2026, 10, 5, h, m, tzinfo=reminders.tz(person)).astimezone(timezone.utc)  # noqa: E731

    early = reminders.check(person, store, ist(7, 30), fake_alert)
    assert early["due"] == [] and [d["status"] for d in early["today"]] == ["upcoming", "upcoming"]

    # 08:02, the server's minute job: family get "time for…", but the spoken
    # reminder is left for the device.
    assert reminders.check(person, store, ist(8, 2), fake_alert, speak=False)["due"] == []
    assert alerts == [("info", alerts[0][1])] and "08:00: time for Asha's Metformin" in alerts[0][1]

    # 08:05: the device gets the reminder once; family aren't told twice.
    assert reminders.check(person, store, ist(8, 5), fake_alert)["due"] == [{"medication": "Metformin", "time": "08:00"}]
    assert reminders.check(person, store, ist(8, 6), fake_alert)["due"] == []
    assert len(alerts) == 1

    # 08:50, still not confirmed: a warning, once.
    assert reminders.check(person, store, ist(8, 50), fake_alert)["escalated"]
    reminders.check(person, store, ist(8, 55), fake_alert)
    assert [a[0] for a in alerts] == ["info", "warning"] and "08:00 Metformin" in alerts[1][1]

    # A dose logged in the person's words counts for the evening slot.
    store.add_dose("asha", "my metformin tablet", True, at=ist(20, 10).isoformat(timespec="seconds"))
    evening = reminders.check(person, store, ist(20, 15), fake_alert)
    assert evening["due"] == [] and evening["today"][1]["status"] == "taken"
    assert evening["today"][0]["status"] == "waiting"


def test_same_medication():
    from kin.reminders import same_medication

    assert same_medication("Metformin", "metformin 500 mg")
    assert same_medication("Amlodipine 5mg", "amlodipine")
    assert not same_medication("Metformin", "Amlodipine")


def test_reminders_peek_changes_nothing(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from kin import mcp_server, reminders

    store = Store(tmp_path / "s.json")
    monkeypatch.setattr(mcp_server, "store", store)
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    person = mcp_server.set_medication_schedule("asha", "Metformin", ["08:00"])
    at = datetime(2026, 10, 5, 9, 0, tzinfo=reminders.tz(person)).astimezone(timezone.utc)
    peek = reminders.check(person, store, at, lambda *a: pytest.fail("peeking must not alert"), deliver=False)
    assert peek["today"][0]["status"] == "waiting" and peek["due"] == [] and not store.recent("reminders", "asha")


def test_family_voice_note_round_trip(tmp_path, monkeypatch):
    import hashlib
    import hmac as hmac_

    from starlette.testclient import TestClient

    from kin import inbox, mcp_server, server

    store = Store(tmp_path / "s.json")
    monkeypatch.setattr(mcp_server, "store", store)
    monkeypatch.setenv("KIN_WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setenv("KIN_API_TOKEN", "tok")
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata", language="Bengali")
    mcp_server.add_family_contact("asha", "Ravi", "whatsapp", "+91 98765 43210", relation="son")

    languages, sent = [], []
    monkeypatch.setattr(inbox, "download_whatsapp_media", lambda media_id: (b"OggS-voice", "audio/ogg"))
    monkeypatch.setattr(inbox, "transcribe", lambda audio, mime, language=None: languages.append(language) or "মা, রবিবার আসছি")
    monkeypatch.setattr(server, "send_whatsapp_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(mcp_server, "send_family_text", lambda contact, text: sent.append((contact["address"], text)))

    payload = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": "919876543210", "type": "audio", "audio": {"id": "media-1", "voice": True}}]}}]}]}).encode()
    sig = "sha256=" + hmac_.new(b"app-secret", payload, hashlib.sha256).hexdigest()
    with TestClient(server.create_app()) as client:
        assert client.post("/whatsapp", content=payload, headers={"X-Hub-Signature-256": sig}).status_code == 200
        assert "voice note for Asha" in sent[-1][1] and languages == ["Bengali"]

        # The device collects it once; peeking doesn't use it up.
        assert len(mcp_server.family_messages("asha")) == 1
        [note] = mcp_server.family_messages("asha", deliver=True)
        assert (note["sender"], note["relation"], note["text"]) == ("Ravi", "son", "মা, রবিবার আসছি")
        assert mcp_server.family_messages("asha", deliver=True) == []

        # The audio is served to the device, behind the API token.
        assert client.get(f"/voice-notes/{note['audio']}").status_code == 401
        audio = client.get(f"/voice-notes/{note['audio']}", headers={"Authorization": "Bearer tok"})
        assert audio.content == b"OggS-voice" and audio.headers["content-type"].startswith("audio/ogg")

    # Asha answers; it goes back to Ravi.
    assert mcp_server.reply_to_family("asha", "my son", "I'm fine, see you Sunday")["sent_to"] == "Ravi"
    assert sent[-1] == ("+91 98765 43210", "💬 Asha says: I'm fine, see you Sunday")
    with pytest.raises(ToolError, match="No family member"):
        mcp_server.reply_to_family("asha", "Meera", "hello")


def test_family_agent_passes_messages(tmp_path, monkeypatch):
    from kin import family_agent, mcp_server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.add_family_contact("asha", "Meera", "whatsapp", "+15550000002", relation="daughter")
    people = family_agent.contacts_for(mcp_server.store, "whatsapp", "15550000002")
    *_, pass_message = family_agent._scoped_tools(people, "whatsapp", "15550000002")
    assert pass_message._tool_func("asha", "I'll call you tonight, Ma") == {"passed_on_to": "Asha"}
    [note] = mcp_server.family_messages("asha")
    assert (note["sender"], note["text"], note["audio"]) == ("Meera", "I'll call you tonight, Ma", None)


async def test_look_up_returns_matching_passages():
    from kin import facts

    def handler(request):
        assert request.url.params["srsearch"] == "national animal of India"
        return httpx.Response(200, json={"query": {
            "search": [{"title": "Tigers in India", "snippet": "Tigers are the <span class=\"searchmatch\">national animal</span> there"}],
            "pages": [{"title": "Tigers in India", "extract": "Tigers in India constituted more than 75% of the global tiger population."}],
        }})

    [hit] = await facts.look_up("national animal of India", transport=httpx.MockTransport(handler))
    assert hit["passage"] == "Tigers are the national animal there"
    assert hit["intro"].startswith("Tigers in India")


def test_speech_route(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.setenv("KIN_API_TOKEN", "tok")

    async def fake_synthesise(text, lang):
        return f"MP3:{lang}:{text}".encode()

    monkeypatch.setattr(server.speech, "synthesise", fake_synthesise)
    auth = {"Authorization": "Bearer tok"}
    with TestClient(server.create_app()) as client:
        assert client.post("/speech", json={"text": "নমস্কার", "lang": "bn"}).status_code == 401
        ok = client.post("/speech", json={"text": "নমস্কার", "lang": "bn"}, headers=auth)
        assert ok.headers["content-type"] == "audio/mpeg" and ok.content == "MP3:bn:নমস্কার".encode()
        assert client.post("/speech", json={"text": "hi", "lang": "xx"}, headers=auth).status_code == 400


def test_whatsapp_webhook_acknowledges_even_if_reply_fails(tmp_path, monkeypatch):
    import hashlib
    import hmac as hmac_

    from starlette.testclient import TestClient

    from kin import mcp_server, server

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    monkeypatch.setenv("KIN_WHATSAPP_APP_SECRET", "app-secret")

    def expired(to, text):
        raise httpx.HTTPStatusError("401", request=httpx.Request("POST", "https://graph.facebook.com"), response=httpx.Response(401))

    monkeypatch.setattr(server, "send_whatsapp_text", expired)
    payload = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": "15550009999", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
    sig = "sha256=" + hmac_.new(b"app-secret", payload, hashlib.sha256).hexdigest()
    with TestClient(server.create_app()) as client:
        assert client.post("/whatsapp", content=payload, headers={"X-Hub-Signature-256": sig}).status_code == 200


@pytest.mark.parametrize("auto,forced,expected", [
    (("english", "Hi Ma"), None, "Hi Ma"),                     # English is trusted
    (("hindi", "नो मुश्कर"), "নমস্কার মা", "নমস্কার মা"),          # Bengali misheard as Hindi
    (("hindi", "नमस्ते, आज मेरी तबियत"), "", "नमस्ते, आज मेरी तबियत"),  # really Hindi: keep it
])
def test_family_note_language(monkeypatch, auto, forced, expected):
    from kin import inbox

    monkeypatch.setenv("KIN_OPENAI_API_KEY", "k")
    calls = []

    def fake_post(url, data, **_):
        calls.append(data.get("language"))
        body = {"language": auto[0], "text": auto[1]} if "language" not in data else {"text": forced}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(inbox.httpx, "post", fake_post)
    assert inbox.transcribe(b"ogg", "audio/ogg", "Bengali") == expected
    assert calls == ([None] if forced is None else [None, "bn"])


async def test_speech_falls_back_to_google(monkeypatch):
    from kin import speech

    async def refused(text, voice):
        raise RuntimeError("no audio")

    async def google(text, lang):
        return f"google:{lang}".encode()

    async def no_wait(_):
        return None

    monkeypatch.setattr(speech, "_edge", refused)
    monkeypatch.setattr(speech, "_google", google)
    monkeypatch.setattr(speech.asyncio, "sleep", no_wait)
    assert await speech.synthesise("নমস্কার", "bn") == b"google:bn"


def test_taken_scheduled_dose_tells_family(tmp_path, monkeypatch):
    from kin import mcp_server, reminders

    monkeypatch.setattr(mcp_server, "store", Store(tmp_path / "s.json"))
    told = []
    monkeypatch.setattr(mcp_server, "notify_family", lambda person, level, reason: told.append(reason) or [])
    mcp_server.register_person("asha", "Asha", 1948, "Kolkata")
    mcp_server.set_medication_schedule("asha", "debza", ["20:30"])
    monkeypatch.setattr(reminders, "scheduled_time", lambda person, med, now=None: "20:30")
    mcp_server.log_medication("asha", "Debza tablet", True)
    assert told == ["✅ Asha took the 20:30 Debza tablet."]

    monkeypatch.setattr(reminders, "scheduled_time", lambda person, med, now=None: None)
    mcp_server.log_medication("asha", "vitamin D", True)  # not scheduled: no message
    assert len(told) == 1
