import io
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from sqlalchemy import func, select

from knowledge.api import create_app
from knowledge.db import engine_for, initialize, turn_artifacts
from knowledge.provider import ProviderBackoff
from knowledge.security import provision
from knowledge.service import Generator, Service
from knowledge.sheet_report import choose_plan
from scripts.test_sales_report_api import MONTHS, QUESTION, SALES


@pytest.fixture
def report_system(tmp_path, monkeypatch):
    eng = engine_for("sqlite:///" + (tmp_path / "reports.db").as_posix())
    initialize(eng)
    with eng.begin() as c:
        a, b = (
            {"Authorization": "Bearer " + provision(c, n, "employee")}
            for n in ("owner", "other")
        )
    plans = []

    def planner(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        data = json.loads(body["messages"][-1]["content"])
        assert set(data) == {"request", "columns"}
        assert "เหนือ" not in str(data) and "18000" not in str(data)
        plans.append(data)
        metric = "c2" if "missing" not in data["request"] else "invalid"
        response = {
            "metric": metric,
            "group": "c0",
            "summarize": True,
            "chart": True,
            "powerpoint": "PowerPoint" in data["request"],
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(response)}}]}
        )

    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://fixture.invalid/v1")
    with httpx.Client(transport=httpx.MockTransport(planner)) as upstream:
        service = Service(eng, None, Generator(client=upstream))
        with TestClient(create_app(service)) as api:
            yield api, a, b, plans, service
    eng.dispose()


def attach(api, owner):
    chat = "/api/conversations/" + str(uuid4())
    example = api.get("/static/samples/sales-q1.xlsx")
    assert example.status_code == 200
    response = api.post(
        chat + "/attachments",
        params={"name": "sales.xlsx"},
        content=example.content,
        headers=owner,
    )
    assert response.status_code == 201
    return chat, {
        "turn_id": str(uuid4()),
        "attachment_id": response.json()["id"],
        "question": QUESTION,
    }


def test_model_chains_tools_and_commits_deck_once(report_system):
    api, a, b, plans, service = report_system
    chat, message = attach(api, a)
    frames = [
        json.loads(line)
        for line in api.post(
            chat + "/stream", json=message, headers=a
        ).text.splitlines()
    ]
    result = frames[-1]["value"]
    assert result["steps"] == [
        "access",
        "file",
        "plan",
        "aggregate",
        "chart",
        "powerpoint",
    ]
    assert result["selected_tools"] == ["summarize", "chart", "powerpoint"]
    assert result["summary"]["total"] == "85500" and result["summary"]["records"] == 6
    assert result["planner"] == "model" and len(plans) == 1
    attachment_preview = chat + "/attachments/" + message["attachment_id"] + "/preview"
    preview = api.get(attachment_preview, headers=a).json()
    assert preview["headers"] == ["เดือน", "สาขา", "ยอดขาย (บาท)", "จำนวนบิล"]
    assert preview["row_count"] == 7
    assert api.get(attachment_preview, headers=b).status_code == 404
    assert (
        api.get(
            attachment_preview.replace(chat, "/api/conversations/" + str(uuid4())),
            headers=a,
        ).status_code
        == 404
    )
    path = result["artifact"]["url"]
    deck_preview = api.get(path + "?preview=true", headers=a).json()
    slides = deck_preview["slides"]
    assert "85,500.00" in " ".join(slides[0]["text"])
    assert [point["label"] for point in slides[1]["points"]] == MONTHS
    assert [point["value"] for point in slides[1]["points"]] == SALES
    assert slides[2]["table"][1][0] == MONTHS[0]
    assert api.get(path + "?preview=true", headers=b).status_code == 404
    assert api.get(path, headers=b).status_code == 404
    assert (
        api.post(
            "/api/conversations/" + str(uuid4()) + "/messages", headers=b, json=message
        ).status_code
        == 404
    )
    assert (
        api.post(chat + "/messages", headers=a, json=message).json() == result
        and len(plans) == 1
    )
    first = api.get(path, headers=a)
    assert api.get(path, headers=a).content == first.content
    deck = Presentation(io.BytesIO(first.content))
    chart = next(s.chart for slide in deck.slides for s in slide.shapes if s.has_chart)
    assert [c.label for c in chart.plots[0].categories] == MONTHS
    assert list(chart.series[0].values) == SALES
    assert "85,500.00" in " ".join(
        s.text for s in deck.slides[0].shapes if s.has_text_frame
    )
    assert (
        api.get(chat, headers=a).json()[0]["result"]["artifact"] == result["artifact"]
    )
    with service.engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(turn_artifacts)) == 1


def test_validate_plan_and_respect_requested_outputs(report_system):
    api, a, _, plans, _ = report_system
    chat, payload = attach(api, a)
    bad = api.post(
        chat + "/messages",
        headers=a,
        json={**payload, "question": "missing chart column"},
    )
    assert bad.status_code == 503
    assert api.get(chat, headers=a).json() == []
    answer = api.post(
        chat + "/messages", headers=a, json={**payload, "question": "สรุปยอดพร้อมกราฟ"}
    )
    assert answer.status_code == 200
    assert "artifact" not in answer.json() and answer.json()["selected_tools"] == [
        "summarize",
        "chart",
    ]
    assert len(plans) == 2


def test_anthropic_contract_and_unconfigured_mode(report_system):
    _, _, _, _, service = report_system
    table = {"headers": ["Month", "Sales"], "rows": [["Jan", "12"]]}

    def respond(request):
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "plan_report"}
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "plan_report",
                        "input": {
                            "summarize": True,
                            "chart": False,
                            "powerpoint": False,
                            "metric": "c1",
                            "group": "c0",
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service.generator = SimpleNamespace(
            protocol="anthropic",
            key="test",
            model="mock",
            url="https://fixture.invalid/v1",
            client=client,
            backoff=ProviderBackoff(),
        )
        plan, origin, metric, group = choose_plan(service, "summary of sales", table)
        assert (plan.chart, origin, metric, group) == (False, "model", "Sales", "Month")
        service.generator.url = "https://unconfigured.invalid/v1"
        plan, origin, _, _ = choose_plan(service, QUESTION, table)
        assert origin == "built_in" and plan.powerpoint
