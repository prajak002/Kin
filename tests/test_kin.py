import json

import httpx
import pytest

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

    with pytest.raises(ValueError):
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
