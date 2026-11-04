"""脚本化用户模拟器：BIRD-INTERACT 的 function-driven user simulator 简化版。

plan 2026-11-04-requirement-slot-contract Phase 4。

## 为什么要这个

现有 e2e 的「填卡」是 `_fill_missing_fields`——**盲选第一个选项**。它只能证明
「流程走得通」，证明不了「代理问对了问题」：代理问错了槽位、或者根本没问就自己猜，
照样能跑出一份报告，被当成绿。BIRD-INTERACT 的做法是让用户模拟器**只按 ground truth
回答**，于是「有没有问到关键槽位」「几轮收敛」才有意义。

这里实现的是**零 token 的确定性版本**：GT 由夹具给出，回答规则固定可复现；
LLM 扮演用户的版本（`SLOT_SIM=llm`）另留 env gate，不进 CI。

## 三个度量（对齐 BIRD-INTERACT）

- `priority_question_sr`：GT 里必须问到的槽位是否都被问到；`must_not_ask` 是否没被问；
- `turns_used`：把卡填到 complete 用了几次作答（Efficiency）；
- `follow_up_sr`：追问轮之后，最终口径是否仍收敛到 GT。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

# 卡上 key → GT 字段
_SLOT_TO_TRUTH: dict[str, str] = {
    "metric": "metric",
    "time_range": "time_range",
    "scope": "scope",
    "granularity": "granularity",
    "analysis_intent": "analysis_intent",
}


@dataclass(frozen=True)
class GroundTruth:
    """用户**真正**想要的东西（夹具给定，代理看不到）。"""

    metric: str | None = None
    granularity: str | None = None
    time_range: str | None = None
    scope: tuple[str, ...] = ()
    analysis_intent: str | None = None


@dataclass
class GroundTruthUser:
    """只按 GT 回答的确定性用户。"""

    truth: GroundTruth
    patience: int = 2
    # 观测（由 runner 读）
    asked_slots: list[str] = field(default_factory=list)
    must_not_ask_violations: list[str] = field(default_factory=list)
    answered_slots: list[str] = field(default_factory=list)
    # 夹具里声明的、本轮不该被问的槽位
    must_not_ask: tuple[str, ...] = ()

    # --- 观测 -------------------------------------------------------------
    def observe_card(self, card: dict) -> None:
        """记录卡上出现的澄清项（用于 priority_question_sr）。"""
        for mf in card.get("missing_fields") or []:
            key = _slot_key(mf)
            if key and key not in self.asked_slots:
                self.asked_slots.append(key)
            if key in self.must_not_ask and key not in self.must_not_ask_violations:
                self.must_not_ask_violations.append(key)

    # --- 作答 -------------------------------------------------------------
    def answer_one(self, card: dict) -> dict | None:
        """回答**代理先问的那一个**槽位（模拟「一次只问一个」的交互节奏）。

        顺序必须跟卡上的 `slot_order` 走——那是服务端下发的提问优先级
        （口径 > 时间范围 > 粒度 > 范围 > 视角）。按 `missing_fields` 原始顺序答会
        答错题：实测模拟器先答了 `analysis_intent`（→ overview 把口径留在模型猜的
        销售额），而代理先问的是 `metric`，于是「用户明确要实收金额」这件事根本没进卡。

        返回改好的卡片副本；卡上没有可回答的槽位（或 patience 用尽）→ None。
        """
        if len(self.answered_slots) >= self.patience:
            return None
        filled = copy.deepcopy(card)
        for mf in _ordered_fields(filled):
            key = _slot_key(mf)
            if not key:
                continue
            options = mf.get("options") or []
            if not options:
                continue
            choice = self._choose(key, options)
            if choice is None:
                continue
            mf["selected_value"] = choice if mf.get("kind") != "multiple" else (
                choice if isinstance(choice, list) else [choice]
            )
            for assumption in filled.get("assumptions") or []:
                assumption["accepted"] = True
            if key not in self.answered_slots:
                self.answered_slots.append(key)
            return filled
        return None

    def _choose(self, slot: str, options: list[dict]):
        """按 GT 选选项；GT 没有这一槽位 → 取第一个（「你决定」）。"""
        want = self._truth_value(slot)
        if want:
            wanted = want if isinstance(want, (list, tuple)) else [want]
            picked = [
                opt.get("value")
                for opt in options
                if _option_matches(opt, wanted)
            ]
            if picked:
                return picked if len(picked) > 1 else picked[0]
        return options[0].get("value")

    def _truth_value(self, slot: str):
        attr = _SLOT_TO_TRUTH.get(slot)
        return getattr(self.truth, attr, None) if attr else None


def _slot_key(missing_field: dict) -> str | None:
    key = str(missing_field.get("key") or "")
    if key in _SLOT_TO_TRUTH:
        return key
    if key.startswith("intent") or key.startswith("analysis"):
        return "analysis_intent"
    return None


def _ordered_fields(card: dict) -> list[dict]:
    """按卡上的 `slot_order` 排澄清项（服务端下发的提问优先级）；旧卡没有就原序。"""
    fields = list(card.get("missing_fields") or [])
    order = card.get("slot_order") or []
    if not order:
        return fields

    ranked: list[dict] = []
    for slot in order:
        for field in fields:
            if field in ranked:
                continue
            key = _slot_key(field)
            if key == slot or (slot == "analysis_intent" and key == "analysis_intent"):
                ranked.append(field)
    ranked.extend(f for f in fields if f not in ranked)
    return ranked


def accept_pending_assumptions(card: dict) -> dict | None:
    """卡上没有待问槽位、但还有没表态的假设 → 全部接受（模拟用户「就按你说的」）。

    返回改好的卡；没有待接受假设 → None。
    """
    pending = [
        a for a in (card.get("assumptions") or []) if a.get("accepted") is None
    ]
    if not pending:
        return None
    filled = copy.deepcopy(card)
    for assumption in filled.get("assumptions") or []:
        if assumption.get("accepted") is None:
            assumption["accepted"] = True
    return filled


def _option_matches(option: dict, wanted: list[str]) -> bool:
    """选项是否命中 GT 值：value 去掉 `前缀:` 后等值，或 label 等值/包含。"""
    value = str(option.get("value") or "")
    bare = value.split(":", 1)[1] if ":" in value else value
    label = str(option.get("label") or "")
    for want in wanted:
        target = str(want)
        if bare == target or label == target or target in label:
            return True
    return False


def summarize(cases: list[dict]) -> dict:
    """把逐 case 观测汇总成三个度量（runner 打印用）。"""
    total = len(cases)
    if not total:
        return {"total": 0}
    prq_ok = sum(1 for c in cases if c.get("priority_ok"))
    fu_ok = sum(1 for c in cases if c.get("follow_up_ok"))
    return {
        "total": total,
        "priority_question_sr": round(prq_ok / total, 3),
        "follow_up_sr": round(fu_ok / total, 3),
        "turns_used_avg": round(
            sum(c.get("turns_used", 0) for c in cases) / total, 2
        ),
        "must_not_ask_violations": sum(
            len(c.get("must_not_ask_violations") or []) for c in cases
        ),
        "not_converged": [
            c["id"] for c in cases if not c.get("converged")
        ],
    }
