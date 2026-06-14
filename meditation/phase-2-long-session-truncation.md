# Phase 2 Retrospective — Long Session Truncation

**Date**: 2026-06-14

## Context

Phase 2 retrospective detection 需要将全 session 的所有 turns 传入 LLM prompt，配合 Phase 1 已知 moments 做 synthesis + gap-fill。

## Concern

Session 可能很长（>200 turns），对应的 prompt 可能超过某些 LLM 的 context window。不同模型的 context window 差异大（128K vs 1M），但早期阶段 session 通常不会那么长。

## Decision

**V1 不做截断。** 直接传所有 turns，token 超限由 LLM 的 context window 自然处理。

## Future Work

如果出现以下信号，应认真考虑截断策略：
- 用户 report Phase 2 detection 失败（token 超限 error）
- 典型 session 的 turns 数超过 150-200
- 使用的 detection model context window < 128K

可能的截断策略：首 N turns + 尾 M turns + 中间摘要（"…N turns omitted…"）。
