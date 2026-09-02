"""runner._observe_turn 应取最新 report version（max(version)），不取 versions[0]。

P14 P1 闭环：多版本 case（adjust / 重新生成）触发多条 report_versions，
runner 必须拿真正最新的那条——否则观测数据 stale，dim_results 全错。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from evaluation.checker import ObservedTurn
from evaluation.runner import _observe_turn


def _fake_client_with_versions(versions_payload: list[dict], latest_version: int):
    """mock httpx.Client：GET /sessions/{sid} 返回 versions 列表，GET /reports/{v} 返回 detail。"""
    client = MagicMock()
    session_resp = MagicMock()
    session_resp.status_code = 200
    session_resp.json.return_value = {
        "session": {"report_versions": versions_payload}
    }
    # latest detail 响应
    detail_resp = MagicMock()
    detail_resp.status_code = 200
    detail_resp.json.return_value = {
        "report": {
            "version": latest_version,
            "query_snapshot": {"sql": "SELECT MAX(v) AS latest_marker FROM t WHERE v = ?", "rows": [{"latest_marker": latest_version}]},
            "report_payload": {"answer": {"table": {"columns": ["latest_marker"], "rows": [[latest_version]]}}},
        }
    }
    # GET 顺序：先 /sessions/{sid} → session_resp；再 /reports/{v} → detail_resp
    def get_side_effect(url, headers=None, **_):
        if "/reports/" in url:
            return detail_resp
        return session_resp

    client.get.side_effect = get_side_effect
    return client


def test_observe_turn_picks_latest_version_not_first():
    """3 个 version (1, 2, 3) → 应取 max=3 而非 versions[0]=1。

    验证：runner 应请求 /reports/3（detail_resp.json 注入 latest_marker=3），
    row_count 反映 latest_version=3 注入的数据。
    """
    versions_payload = [
        {"version": 1},
        {"version": 2},
        {"version": 3},
    ]
    client = _fake_client_with_versions(versions_payload, latest_version=3)
    obs, detail = _observe_turn(
        events=[],
        sid="test-sid",
        client=client,
        token="fake",
        executed=True,
    )

    # detail 应是 latest_version=3 的内容
    assert detail.get("version") == 3, f"expected version=3, got {detail.get('version')}"
    # 验证 GET URL 是 /reports/3 而不是 /reports/1
    report_get_urls = [
        call.args[0] for call in client.get.call_args_list
        if "/reports/" in call.args[0]
    ]
    assert any(url.endswith("/reports/3") for url in report_get_urls), (
        f"P14 P1 失守：runner 应请求 /reports/3，实际请求列表 = {report_get_urls}"
    )
    assert not any(url.endswith("/reports/1") for url in report_get_urls), (
        f"P14 P1 失守：runner 不应请求 /reports/1（stale），实际请求列表 = {report_get_urls}"
    )


def test_observe_turn_picks_max_when_versions_unordered():
    """versions 列表无序（[3, 1, 2]）→ 也应取 max=3。"""
    versions_payload = [
        {"version": 3},
        {"version": 1},
        {"version": 2},
    ]
    client = _fake_client_with_versions(versions_payload, latest_version=3)
    obs, detail = _observe_turn(
        events=[],
        sid="test-sid",
        client=client,
        token="fake",
        executed=True,
    )
    assert detail.get("version") == 3


def test_observe_turn_no_versions_returns_empty_obs():
    """空 versions 列表 → obs 字段全空，detail=None。"""
    client = MagicMock()
    session_resp = MagicMock()
    session_resp.status_code = 200
    session_resp.json.return_value = {"session": {"report_versions": []}}
    client.get.return_value = session_resp

    obs, detail = _observe_turn(
        events=[],
        sid="test-sid",
        client=client,
        token="fake",
        executed=True,
    )
    assert detail is None
    assert obs.row_count is None
    assert obs.sql is None
