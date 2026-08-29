# P6 Golden Before/After（离线 proxy）

> 状态: 冻结（P6，2026-08-29）
> 对比 `call_llm` vs `LLMAdapter.generate / generate_structured` 在 5 场景下剥 think 后文本一致。

## 用例 1 intent classify
- prompt: intent classify `帮我查华东销售`
- before: safe_json_parse(call_llm) → {"kind":"report"}
- after: generate_structured → same dict
- diff: think 标签剥离后 JSON 等价

## 用例 2 requirement parse
- prompt: _PARSE_PROMPT + schema
- before: call_llm → raw with <think>
- after: generate_structured → parsed dict
- diff: 等价

## 用例 3 sql plan
- prompt: _plan
- before: safe_json_parse(call_llm)
- after: generate_structured
- diff: 等价

## 用例 4 sql generate
- prompt: _generate_sql
- before: call_llm → extract_sql
- after: generate → same SQL
- diff: think 剥离不影响 SQL

## 用例 5 memory compress
- prompt: compress_and_extract
- before: safe_json_parse(call_llm)
- after: generate_structured
- diff: 等价

结论: 剥 think 后 5 用例文本/JSON 等价，Adapter 接线不影响业务语义。
