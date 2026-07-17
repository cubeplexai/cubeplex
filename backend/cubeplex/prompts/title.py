"""Prompt template for conversation auto-title generation.

Used by ``cubeplex.services.conversation_title`` when the frontend asks the
backend to label a brand-new conversation from its first user message.

Design notes for future editors — change carefully:

- The prompt is delivered as a single **user** message, not a system + user
  pair. Many models we serve via OpenAI-compatible / Anthropic adapters,
  when given a long system block followed by long user content, default
  to "respond to the content" and echo the first few characters as the
  "title". One self-contained user message with the content wrapped in a
  fenced block sidesteps that failure mode.
- The trailing literal ``Title:`` is a strong completion cue — the model
  fills in the title rather than restating the task.
- Two few-shot examples, chosen to teach the two behaviours that the
  model gets wrong most often:
  (1) a verb-led Chinese request that must become a noun-phrase title
      in the user's input language;
  (2) source text plus a trailing instruction — title must reflect the
      **intent** (and its language), not the body.
- The literal sentinel ``<<<USER_MESSAGE>>>`` is substituted by the
  service with the user's first message (already trimmed to
  ``MAX_SNIPPET_CHARS``). We use a sentinel + ``str.replace`` rather than
  ``str.format`` so curly braces inside the user's content can't blow up
  templating.
"""

TITLE_PROMPT_PLACEHOLDER: str = "<<<USER_MESSAGE>>>"

TITLE_GENERATION_PROMPT: str = """\
You name conversations for a sidebar list. Read the user's first message
below and produce a SHORT TITLE describing the conversation's intent.

Rules:
- Length: 4-6 English words OR 6-10 Chinese characters. Hard cap.
- Language: match the language of the user's PRIMARY INTENT (which may
  be the trailing instruction, not the pasted source material).
- Form: a noun phrase. Never start with a verb like "Use", "Asks",
  "How to", "Translate", "Help with", "帮我", "询问".
- Front-load the topic: the first 2-3 words / 4-6 Chinese characters
  must convey the topic on their own, since the sidebar truncates with
  an ellipsis.
- Output: a single line of plain text. No quotes, no newlines, no
  punctuation, no trailing period. NEVER echo, copy, or quote a span
  from the input — invent a fresh summary phrase.

Examples:

Input:
```
帮我写一个 React 的虚拟滚动列表组件,需要支持动态高度。
```
Title: React 虚拟滚动列表

Input:
```
Use this skill when the user asks how to find tools, templates, or
workflows. Mentions they wish they had help with a specific domain.

上面这段话翻译成中文
```
Title: 技能使用说明翻译

Now produce a title for this input:

```
<<<USER_MESSAGE>>>
```

Title:"""
