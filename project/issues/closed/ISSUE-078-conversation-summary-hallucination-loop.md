# ISSUE-078: Conversation Summary Hallucination Loop

**Status:** Cancelled
**Created:** 2025-01-28
**Cancelled:** 2026-04-23
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug

## Capture
**Original request:** "Ai ai, lukt niet goed, maak een rapport in de repo van wat fout gaat, analyseer dit gesprek, we zullen later zien hoe we kunnen verbeteren"

**Resolved intent:** Marcel exhibited a looping hallucination bug during a Telegram conversation about Selecta vending machine spending. The root cause appears to be that a previous conversation summary contained hardcoded transaction data, which Marcel then treated as live data and re-executed (including chart generation) on every new user message — even when the user asked a completely different question.

## Description

During a multi-turn Telegram conversation, three distinct failure modes were observed:

### 1. Repeated chart generation on every turn
Each time the user sent a message — regardless of content — Marcel re-generated the same Selecta spending chart with hardcoded data and repeated the same introductory text ("Nu heb ik alle 127 transacties. Tijd voor de grafiek!"). This happened at least 3 times across 3 different user messages.

### 2. Conversation summary treated as live data
The session started with a conversation summary that contained specific Selecta transaction data (127 transactions, prices of €1.07/€1.20, monthly totals, etc.). Marcel interpreted this summarized/historical data as freshly retrieved bank data, and re-used it wholesale in every response — without ever calling the banking integration.

### 3. Hallucinated tool calls
When the user asked "Hoeveel spendeer ik aan cola per maand?" (How much do I spend on cola per month?), Marcel:
- Said "Laat me even door je transacties zoeken" (Let me search your transactions)
- Called `marcel(action="notify")` as a progress update
- Then immediately re-generated the same hardcoded chart without ever calling the banking skill

This is a hallucination: Marcel simulated the appearance of fetching data without actually doing so.

### 4. No self-correction when user hinted at the problem
When the user said "Ik ga eens doen alsof ik de vraag opnieuw stel" (I'll pretend to ask the question again), Marcel did not recognize this as a signal that something was wrong — it simply repeated the same broken behavior a third time.

## Root Cause Hypotheses

1. **Summary replay bug:** The conversation summary contained tool call outputs (chart generation code + result). When the model received this summary, it may have re-executed or replayed those tool calls rather than treating them as historical context.

2. **Anchoring on summary data:** The model saw specific transaction data in the summary and anchored on it, skipping actual data retrieval because "the data was already there."

3. **Missing guardrail for stale context:** There is no mechanism to detect when a conversation summary contains results from a previous session that should not be re-executed in the current session.

4. **Chart generation code in summary:** The summary appears to have included the full `generate_chart` call with hardcoded transaction lists. This may have caused the model to reproduce the entire code block in subsequent responses.

## Tasks
- [ ] Investigate how conversation summaries are generated — do they include tool call code/results?
- [ ] Reproduce the bug: create a summary with embedded tool call data and observe model behavior
- [ ] Determine whether the issue is in summary generation (compaction) or in how the model interprets summaries
- [ ] Add a guardrail or instruction to prevent re-execution of tool calls from summaries
- [ ] Consider stripping or abstracting tool call outputs in compaction (store results, not code)
- [ ] Add a self-correction signal: if the same chart/output is produced 2+ times in a row, flag it

## Observed Behavior Timeline

| Turn | User message | Marcel behavior |
|------|-------------|----------------|
| 1 | (session start with summary) | Generated chart + "Nu heb ik alle 127 transacties" |
| 2 | "Ik ga eens doen alsof ik de vraag opnieuw stel" | Generated same chart again |
| 3 | "Hoeveel spendeer ik aan cola per maand?" | Generated same chart twice + fake notify call |

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Cancellation (2026-04-23)

Cancelled as stale. The reported failure mode — conversation summaries containing tool-call *code* which the model then re-executed — cannot occur under the summarization architecture that has shipped since this issue was filed (15 months ago).

The current pipeline:

- `src/marcel_core/memory/summarizer.py` — rolling segment summaries, idle-triggered
- `src/marcel_core/harness/runner.py:201-211` — sealed-segment loading, idle summarization check
- Summaries are LLM-compressed prose over sealed segments; tool-call payloads are not preserved verbatim in the summary stream

If a similar loop is observed under the current architecture, open a fresh issue with a reproducible transcript — the old hypotheses here are about a pipeline that no longer exists.
