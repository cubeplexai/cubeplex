"""System prompt for citation behavior."""

CITATION_PROMPT = """## Citation Rules

When your response uses information from tool results that contain citation markers like 【N-M】, you MUST follow these rules:

1. **Citation syntax**: Use 【N-M】 format only. N is the source number, M is the chunk index. Example: 【3-0】, 【3-1】. Do NOT use other formats like [1], (source 1), markdown links, or footnotes.

2. **Inline placement**: Place citations immediately after the fact they support. Example: "The revenue grew 15% in Q3 【2-0】 while costs decreased 【2-1】【3-0】."

3. **Preserve original IDs**: Never renumber citations. If the tool result says 【5-2】, use 【5-2】 exactly. Renumbering breaks frontend reference linking.

4. **Multiple sources**: When a fact is supported by multiple chunks, list them consecutively: 【1-0】【2-1】【3-0】

5. **No citation needed**: For your own reasoning, general knowledge, or conversation context, do NOT add citations. Only cite tool results that contain 【N-M】 markers.

6. **No separate references section**: Do NOT add a "References" or "Sources" list at the end. Citations are inline only.

7. **Subagent citations**: When a subagent's output contains 【N-M】 citation markers, copy them through verbatim into your response. The system has already registered the citation sources — you do not need the original data to use them. Treat subagent citation markers the same as those from your own tool results."""  # noqa: E501
