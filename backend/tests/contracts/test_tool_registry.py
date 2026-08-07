"""工具注册表契约：字典工具在册、元数据正确、SQL 门控工具集不变。"""
import pytest

pytestmark = pytest.mark.contracts


def test_search_interface_dictionary_registered():
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    meta = registry.get_metadata("search_interface_dictionary")
    assert meta is not None
    assert meta.agent_type == "data"
    assert meta.capability == "dictionary_search"
    assert meta.risk_level == "low"


def test_sql_tools_still_registered():
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    for name in ("validate_sql", "execute_sql"):
        assert registry.get_metadata(name) is not None