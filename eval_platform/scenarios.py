"""场景测试（多步、有状态）：测「小功能」和细节，不是单点问答。

单条用例（`runners.py`）回答「这条 query 命中了吗」；场景回答更接近工程事故的问题：

| 场景 | 要证明什么 |
|---|---|
| `rag-incremental-upsert` | 同一文档**增量更新**：旧内容被替换、文档数不增长、新内容可召回 |
| `rag-delete-no-dirty-recall` | 删文档后**无脏召回**（删了就别再返回它） |
| `rag-kb-isolation` | pin 别的知识库时**不会串**到本库内容（防跨库污染） |

纪律：每个场景自建**临时知识库**（`eval-tmp-*`），跑完删干净；绝不碰生产库（三一重工年报）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable

from eval_platform import adapters

_POLL_INTERVAL_S = 1.5
_INDEX_TIMEOUT_S = float(300)


def _token(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _doc_text(title: str, token: str, extra: str = "") -> bytes:
    """一份可被切块、含唯一 token 的 markdown（token 用来做检索命中判定）。"""
    body = (
        f"# {title}\n\n"
        f"评测临时文档，编号 {token}。\n\n"
        f"- 关键标识：{token}\n"
        f"- 用途：验证增量更新与删除语义\n"
        f"- 无关内容：华东区域月度销售趋势、门店明细、口径说明。{extra}\n"
    )
    return body.encode("utf-8")


async def _wait_indexed(kb_id: str, document_id: str) -> dict[str, Any]:
    """等入库完成（后台任务）：状态到 indexed/failed 或超时。

    真机实测：一份 3 行 markdown 走完分块 + embedding 要 **141s**（2026-11-17，硅基流动 embedding），
    所以默认等待 300s；超时是**工具上限**，不是产品失败（调用方据此判 `timeout` 而非 `fail`）。
    """
    deadline = time.monotonic() + _INDEX_TIMEOUT_S
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        docs = await adapters.ragent_docs(kb_id)
        last = next((d for d in docs if str(d.get("document_id")) == str(document_id)), {})
        if last.get("status") in ("indexed", "failed"):
            return last
        await asyncio.sleep(_POLL_INTERVAL_S)
    return last or {"status": "timeout"}


def _is_ready(doc: dict[str, Any]) -> bool:
    return doc.get("status") == "indexed"


def _index_problem(doc: dict[str, Any]) -> tuple[str, str]:
    """把入库结果翻译成 (step_status, detail)：超时/失败/成功三态。"""
    status = str(doc.get("status") or "")
    if status == "indexed":
        return "pass", f"status=indexed chunks={doc.get('chunk_count')} embedded={doc.get('embedded_chunk_count')}"
    if status == "timeout":
        return "timeout", f"入库等待超过 {_INDEX_TIMEOUT_S:.0f}s（工具上限，不代表产品失败）"
    return "fail", f"status={status} error={doc.get('error_message') or '-'}"


def _hit(items: list[dict[str, Any]], token: str, doc_id: str | None = None) -> tuple[bool, bool]:
    """返回 (文档命中, 文本命中)。"""
    docs = [str(it.get("document_id")) for it in items]
    text = "\n".join(str(it.get("text") or "") for it in items).replace(" ", "")
    doc_hit = doc_id is None or doc_id in docs
    return doc_hit, token.replace(" ", "") in text


class _Run:
    """收集步骤，最后算 verdict。"""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False
        self.timed_out = False

    def step(self, name: str, ok: bool, detail: str = "", *, status: str | None = None,
             **data: Any) -> bool:
        resolved = status or ("pass" if ok else "fail")
        self.steps.append({"name": name, "status": resolved, "detail": detail, "data": data})
        if resolved == "fail":
            self.failed = True
        elif resolved == "timeout":
            self.timed_out = True
        return resolved == "pass"

    @property
    def verdict(self) -> str:
        if self.failed:
            return "fail"
        if self.timed_out:
            return "error"          # 工具等待超限：既不是 pass 也不是产品 fail
        return "pass"


async def _temp_kb(name: str) -> tuple[str, str]:
    """建一个临时知识库，返回 (kb_id, name)。"""
    kb_name = f"eval-tmp-{name}-{uuid.uuid4().hex[:6]}"
    kb = await adapters.ragent_create_kb(kb_name)
    return str(kb["id"]), kb_name


async def _drop_kb(kb_id: str) -> None:
    """尽力清理：先删文档再删库。"""
    try:
        for doc in await adapters.ragent_docs(kb_id):
            await adapters.ragent_delete_doc(str(doc.get("document_id")))
    except Exception:  # noqa: BLE001 - 清理失败不影响结论
        pass
    await adapters.ragent_delete_kb(kb_id)


# --- 场景 1：增量更新 -------------------------------------------------------


async def _scenario_incremental(run: _Run) -> dict[str, Any]:
    token_a, token_b = _token("ALPHA"), _token("BETA")
    kb_id, kb_name = await _temp_kb("incr")
    try:
        up = await adapters.ragent_upload(kb_id, "incr-doc.md", _doc_text("增量更新 v1", token_a))
        doc_id = str(up["document_id"])
        doc = await _wait_indexed(kb_id, doc_id)
        st, detail = _index_problem(doc)
        if not run.step("v1 入库", st == "pass", detail, status=st):
            run.step("v1 可召回", False, "入库未就绪，跳过", status="skipped")
            return {"kb": kb_name, "doc_ids": [doc_id]}

        got = await adapters.ragent_retrieve(token_a, kb_ids=[kb_id])
        hit_doc, hit_text = _hit(got.data["items"], token_a, doc_id)
        run.step("v1 可召回", hit_doc and hit_text,
                 f"items={len(got.data['items'])} doc_hit={hit_doc} text_hit={hit_text}")

        # 同一 document_id 再上传 = 增量更新（覆盖），不是新增
        up2 = await adapters.ragent_upload(
            kb_id, "incr-doc.md", _doc_text("增量更新 v2", token_b), document_id=doc_id,
        )
        doc2 = await _wait_indexed(kb_id, str(up2["document_id"]))
        st2, detail2 = _index_problem(doc2)
        if not run.step("v2 入库", st2 == "pass", detail2, status=st2):
            run.step("覆盖而非新增", False, "入库未就绪，跳过", status="skipped")
            return {"kb": kb_name, "doc_ids": [doc_id]}

        docs_after = await adapters.ragent_docs(kb_id)
        run.step("覆盖而非新增", len(docs_after) == 1,
                 f"文档数={len(docs_after)}（期望 1）", docs=[d.get("filename") for d in docs_after])

        got_b = await adapters.ragent_retrieve(token_b, kb_ids=[kb_id])
        b_doc, b_text = _hit(got_b.data["items"], token_b, doc_id)
        run.step("v2 新内容可召回", b_doc and b_text, f"doc_hit={b_doc} text_hit={b_text}")

        got_a = await adapters.ragent_retrieve(token_a, kb_ids=[kb_id])
        a_doc, a_text = _hit(got_a.data["items"], token_a)
        run.step("v1 旧内容已被替换", not a_text,
                 f"旧 token 仍出现在召回文本中={a_text}（期望 False）")
        return {"kb": kb_name, "doc_ids": [doc_id]}
    finally:
        await _drop_kb(kb_id)


# --- 场景 2：删除后无脏召回 -------------------------------------------------


async def _scenario_delete(run: _Run) -> dict[str, Any]:
    token = _token("DELETE")
    kb_id, kb_name = await _temp_kb("del")
    try:
        up = await adapters.ragent_upload(kb_id, "del-doc.md", _doc_text("删除验证", token))
        doc_id = str(up["document_id"])
        doc = await _wait_indexed(kb_id, doc_id)
        st, detail = _index_problem(doc)
        if not run.step("入库", st == "pass", detail, status=st):
            run.step("删除前可召回", False, "入库未就绪，跳过", status="skipped")
            return {"kb": kb_name, "doc_id": doc_id}

        before = await adapters.ragent_retrieve(token, kb_ids=[kb_id])
        hit_doc, hit_text = _hit(before.data["items"], token, doc_id)
        run.step("删除前可召回", hit_doc and hit_text, f"doc_hit={hit_doc} text_hit={hit_text}")

        ok = await adapters.ragent_delete_doc(doc_id)
        run.step("删除接口返回成功", ok, f"document_id={doc_id}")

        docs = await adapters.ragent_docs(kb_id)
        run.step("文档列表已清空", len(docs) == 0, f"剩余={len(docs)}")

        after = await adapters.ragent_retrieve(token, kb_ids=[kb_id])
        _, after_text = _hit(after.data["items"], token)
        run.step("删除后无脏召回", not after_text,
                 f"items={len(after.data['items'])} 旧 token 仍出现={after_text}")
        return {"kb": kb_name, "doc_id": doc_id}
    finally:
        await _drop_kb(kb_id)


# --- 场景 3：跨库隔离 -------------------------------------------------------


async def _scenario_isolation(run: _Run) -> dict[str, Any]:
    token = _token("ISOLATED")
    kb_id, kb_name = await _temp_kb("iso")
    try:
        up = await adapters.ragent_upload(kb_id, "iso-doc.md", _doc_text("隔离验证", token))
        doc_id = str(up["document_id"])
        doc = await _wait_indexed(kb_id, doc_id)
        st, detail = _index_problem(doc)
        if not run.step("临时库入库", st == "pass", detail, status=st):
            run.step("本库可召回", False, "入库未就绪，跳过", status="skipped")
            return {"kb": kb_name}

        own = await adapters.ragent_retrieve(token, kb_ids=[kb_id])
        hit_doc, hit_text = _hit(own.data["items"], token, doc_id)
        run.step("本库可召回", hit_doc and hit_text, f"doc_hit={hit_doc} text_hit={hit_text}")

        prod_kbs = await adapters.ragent_kb_ids(await adapters.ragent_login())
        other = await adapters.ragent_retrieve(token, kb_ids=prod_kbs)
        leaked = [str(it.get("document_id")) for it in other.data["items"] if str(it.get("document_id")) == doc_id]
        run.step("pin 其他库时不串本库文档", not leaked,
                 f"生产库 kb={prod_kbs} 返回条目={len(other.data['items'])} 泄漏={leaked}")
        return {"kb": kb_name, "prod_kb": prod_kbs}
    finally:
        await _drop_kb(kb_id)


# --- 注册表 -----------------------------------------------------------------

_SCENARIOS: dict[str, dict[str, Any]] = {
    "rag-incremental-upsert": {
        "id": "rag-incremental-upsert",
        "name": "增量更新（同一文档覆盖）",
        "project": "ragent",
        "desc": "同一 document_id 重新上传：文档数不增长、新内容可召回、旧内容被替换",
        "steps": ["v1 入库", "v1 可召回", "v2 入库", "覆盖而非新增", "v2 新内容可召回", "v1 旧内容已被替换"],
        "fn": _scenario_incremental,
    },
    "rag-delete-no-dirty-recall": {
        "id": "rag-delete-no-dirty-recall",
        "name": "删除后无脏召回",
        "project": "ragent",
        "desc": "删文档后：列表清空、检索不再返回该文档内容",
        "steps": ["入库", "删除前可召回", "删除接口返回成功", "文档列表已清空", "删除后无脏召回"],
        "fn": _scenario_delete,
    },
    "rag-kb-isolation": {
        "id": "rag-kb-isolation",
        "name": "跨库隔离（pin kb_ids）",
        "project": "ragent",
        "desc": "指定别的知识库检索时，不得返回本库文档（防跨库污染）",
        "steps": ["临时库入库", "本库可召回", "pin 其他库时不串本库文档"],
        "fn": _scenario_isolation,
    },
}


def list_scenarios() -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if k != "fn"} for s in _SCENARIOS.values()]


async def run_scenario(scenario_id: str) -> dict[str, Any]:
    scenario = _SCENARIOS.get(scenario_id)
    if scenario is None:
        raise KeyError(f"scenario not found: {scenario_id}")
    run = _Run()
    started = time.monotonic()
    try:
        data = await scenario["fn"](run)
        error = None
    except adapters.ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 场景里任何未预期异常都记成 error
        data = {}
        error = f"{type(exc).__name__}: {exc}"
        run.step("场景执行", False, error)
    return {
        "scenario_id": scenario_id,
        "name": scenario["name"],
        "project": scenario["project"],
        "desc": scenario["desc"],
        "verdict": "error" if error else run.verdict,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "steps": run.steps,
        "data": data,
        "error": error,
    }
