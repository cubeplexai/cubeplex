"""System prompt for conversation auto-title generation.

Used by ``cubebox.services.conversation_title`` when the frontend asks the
backend to label a brand-new conversation from its first user message.
"""

TITLE_GENERATION_SYSTEM_PROMPT: str = """\
Write a conversation title for a sidebar list. Strict rules:
- Match the user's language exactly (Chinese in, Chinese out; English in,
  English out).
- 4-6 English words, OR 6-10 Chinese characters. Never longer.
- Front-load the core topic: the first 2-3 words (first 4-6 Chinese
  characters) must be enough to recognise the conversation if the rest is
  truncated with an ellipsis.
- Use a noun phrase. No leading verb like '帮我'/'询问'/'how to'/'help
  with'. No punctuation. No quotes. No trailing period.
Return ONLY the title text.
"""
