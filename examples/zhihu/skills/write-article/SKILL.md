---
name: write-zhihu-article
description: Write a compelling Zhihu article tuned to platform algorithm and reader expectations
version: 1.0.0
tags: [writing, zhihu, content]
triggers:
  - user wants to write an article for Zhihu
  - user provides a topic and asks for a long-form post
  - user shares hot questions and asks for an answer
anti_triggers:
  - user wants short-form content (tweet, status)
  - user wants code or technical documentation
  - user wants Yanxuan paid fiction (use salt-story skill instead)
outcome_metrics: true
update_section: '## Reference Data'
---

## Instructions

Write a Zhihu article following these guidelines:

1. **Length**: 2000-4000 words. Sweet spot for Zhihu algorithm completion rate.
2. **Style**: practical, concrete, with real examples and specific numbers.
3. **Avoid AI-sounding phrases**:
   - NO "这不仅仅是A，更是B"
   - NO "某种意义上来说"
   - NO perfectly balanced viewpoints
4. **Voice**: include personal opinions and real-world pitfalls. Sound like a person.
5. **Hooks**: open with a question, contradiction, or specific scene — never with "随着...的发展".
6. **Conclusion**: avoid "总之" / "综上". End with a concrete takeaway or open question.

Refer to `references/anti-ai.md` for the full anti-AI checklist when reviewing your draft.
Refer to `references/style-guide.md` for tone and structure deep dive.

## Triggers

When to use this skill:

- The user gives a Zhihu hot question and asks for an answer
- The user provides a topic and explicitly says "write me a Zhihu article"
- The user shares a knowledge base entry and asks to publish it on Zhihu

## Anti-Triggers

When NOT to use this skill:

- The user wants a tweet, status update, or short-form content
- The user wants Python code, SQL, or technical documentation
- The user wants paid fiction for Yanxuan (use `salt-story` instead)
- The user wants a WeChat Official Account article (different platform conventions)

## Examples

### Example 1: Hot question response

**Input**: "知乎热门：为什么大厂喜欢用 Java 而不是 Go？"

**Output approach**:

- Open with a contrarian or specific take ("不是大厂喜欢 Java，是 Java 嫁妆太多")
- Body alternates: real anecdote → technical reason → counter-example
- Close with an actionable takeaway for the reader's situation

### Example 2: Knowledge base → article

**Input**: User provides notes on "Postgres index types"

**Output approach**:

- Reframe as a problem the reader has ("线上慢查询排查到一半发现索引建错了")
- Explain via the reader's narrative arc, not the textbook order
- Include one war story per index type if available

## Reference Data

_This section is auto-updated by the aiflay iteration loop based on real Zhihu performance metrics._

(Will be populated after the first review cycle)
