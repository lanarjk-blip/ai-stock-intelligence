import importlib

import requests

import sitecustomize


class FakeResponse:
    def __init__(self, status_code=200, usable=True):
        self.status_code = status_code
        self._usable = usable

    def json(self):
        if not self._usable:
            return {"chart": {"result": []}}
        return {
            "chart": {
                "result": [
                    {
                        "timestamp": [1, 2],
                        "indicators": {
                            "quote": [{"close": [7000.0, 7010.0]}]
                        },
                    }
                ]
            }
        }


def test_ihsg_failover_runs_when_query1_raises(monkeypatch):
    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append(str(url))
        if "query1.finance.yahoo.com" in str(url):
            raise requests.Timeout("query1 timeout")
        return FakeResponse(200, usable=True)

    monkeypatch.setattr(sitecustomize, "_ORIGINAL_GET", fake_get)
    response = sitecustomize._get_with_ihsg_failover(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EJKSE",
        timeout=1,
    )

    assert sitecustomize._usable_chart_response(response)
    assert len(calls) == 2
    assert "query1.finance.yahoo.com" in calls[0]
    assert "query2.finance.yahoo.com" in calls[1]


def test_ihsg_failover_runs_when_query1_returns_empty(monkeypatch):
    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append(str(url))
        if "query1.finance.yahoo.com" in str(url):
            return FakeResponse(200, usable=False)
        return FakeResponse(200, usable=True)

    monkeypatch.setattr(sitecustomize, "_ORIGINAL_GET", fake_get)
    response = sitecustomize._get_with_ihsg_failover(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EJKSE",
        timeout=1,
    )

    assert sitecustomize._usable_chart_response(response)
    assert len(calls) == 2
    assert "query2.finance.yahoo.com" in calls[1]


def test_non_ihsg_request_is_not_rewritten(monkeypatch):
    calls = []
    expected = FakeResponse(200, usable=True)

    def fake_get(url, *args, **kwargs):
        calls.append(str(url))
        return expected

    monkeypatch.setattr(sitecustomize, "_ORIGINAL_GET", fake_get)
    response = sitecustomize._get_with_ihsg_failover(
        "https://query1.finance.yahoo.com/v8/finance/chart/BBCA.JK",
        timeout=1,
    )

    assert response is expected
    assert calls == ["https://query1.finance.yahoo.com/v8/finance/chart/BBCA.JK"]
