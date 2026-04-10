---
name: memory
description: Manage conversation memory — search past conversations, recall facts, and compress the current conversation
requires: {}
---

# Memory Management

Marcel has three memory tools that work together to give you both short-term conversational recall and long-term fact memory.

## Tools

### memory_search(query)
Search extracted facts (preferences, contacts, decisions, schedules).
**Use when:** the user asks "what's my...", "do you remember my...", or any factual recall.

### conversation_search(query)
Search past conversation segments by keyword. Returns matching messages with surrounding context from older (summarized) conversation segments.
**Use when:** the user asks "remember when we talked about...", "what did you say about...", or when you need context from a past discussion that isn't in your current context window.

### compact_now()
Manually compress the current conversation segment into a summary. Opens a fresh segment.
**Use when:** the topic has shifted significantly, the user asks to "clean up" or "compress", or the context feels cluttered.

## Patterns

- When the user references something from the past, try `memory_search` first (fast, factual). If that misses, fall back to `conversation_search` (broader, contextual).
- Don't search proactively — only when the user's question requires historical context that isn't already in your conversation summary.
- After compaction, briefly mention what key points were preserved so the user knows what you'll remember.
- The `/forget` command (Telegram) triggers the same compaction as `compact_now`.

## How memory works

Your conversation is one continuous thread per channel. Active conversation messages stay in full context. When the conversation goes quiet for an hour (or the user says `/forget`), the active segment is summarized into a rolling summary. Each summary absorbs the previous one, so you always have a compressed view of the full conversation history — like human memory, recent things are vivid and older things are gist-only.
