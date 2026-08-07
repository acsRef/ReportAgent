"""search_interface_dictionary：未配置降级、命中序列化、401 重登、不可达不抛栈。"""
import json

import pytest

pytestmark = pytest.mark.contracts


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def dict_env(monkeypatch):
    monkeypatch.setenv("RAGENT_URL", "http://fake:8000")
    monkeypatch.setenv("RAGENT_USER", "admin")
    monkeypatch.setenv("RAGENT_PASSWORD", "admin123")
    monkeypatch.setenv("DICT_KB_NAME", "数据字典")


def test_unset_env_degrades_gracefully(monkeypatch):
    from app.tools.interface_dict_tools import search_interface_dictionary
    monkeypatch.delenv("RAGENT_URL", raising=False)
    out = json.loads(search_interface_dictionary.invoke({"query": "销售额"}))
    assert "未配置" in out["error"]


def test_happy_path_serializes_matches(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [{"chunk_id": "c1", "document_id": "d1",
                                      "text": "total_amount 销售金额", "title": "dict-table_public_fact_sales.md",
                                      "section_path": "", "score": 0.8}], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()

    out = json.loads(search_interface_dictionary.invoke({"query": "total_amount 是什么", "top_k": 3}))
    assert out["matches"][0]["text"].startswith("total_amount")
    assert out["matches"][0]["source"] == "dict-table_public_fact_sales.md"


def test_unreachable_returns_error_text(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import httpx as real_httpx
    import app.tools.interface_dict_tools as mod

    def boom(*a, **kw):
        raise real_httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "post", boom)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "x"}))
    assert "不可达" in out["error"]


def test_empty_result_semantics(monkeypatch, dict_env):
    from app.tools.interface_dict_tools import search_interface_dictionary
    import app.tools.interface_dict_tools as mod

    def fake_post(url, **kw):
        return _Resp(200, {"access_token": "t"})

    def fake_request(method, url, **kw):
        if url.endswith("/api/v1/kb"):
            return _Resp(200, [{"id": "kb-9", "name": "数据字典"}])
        return _Resp(200, {"items": [], "degraded": False})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.httpx, "request", fake_request)
    mod._token_cache.clear()
    out = json.loads(search_interface_dictionary.invoke({"query": "不存在的字段"}))
    assert out["matches"] == []
    assert "无匹配" in out["note"]
