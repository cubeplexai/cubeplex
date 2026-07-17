"""Tests for snapshot message filtering."""

from cubeplex.services.conversation_sharing import filter_messages_for_snapshot


def _user_msg(text: str, **metadata: object) -> dict[str, object]:
    return {"role": "user", "content": [{"type": "text", "text": text}], "metadata": metadata}


def _assistant_msg(text: str) -> dict[str, object]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _synthetic_msg() -> dict[str, object]:
    return {
        "role": "user",
        "content": [{"type": "text", "text": "injected"}],
        "metadata": {"synthetic": True},
    }


def _tool_result_msg() -> dict[str, object]:
    return {
        "role": "tool_result",
        "tool_call_id": "tc_1",
        "tool_name": "search",
        "content": [{"type": "text", "text": "results"}],
    }


class TestFilterMessages:
    def test_keeps_user_and_assistant(self) -> None:
        msgs = [_user_msg("hello"), _assistant_msg("hi")]
        result = filter_messages_for_snapshot(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_excludes_synthetic(self) -> None:
        msgs = [_synthetic_msg(), _user_msg("real")]
        result = filter_messages_for_snapshot(msgs)
        assert len(result) == 1
        assert result[0]["content"][0]["text"] == "real"  # type: ignore[index]

    def test_keeps_tool_result(self) -> None:
        msgs = [_tool_result_msg()]
        result = filter_messages_for_snapshot(msgs)
        assert len(result) == 1

    def test_strips_attachment_content(self) -> None:
        msg = _user_msg(
            "see file",
            attachments=[
                {
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                    "size": 2048,
                    "url": "https://internal/secret-url",
                    "content": "base64data",
                }
            ],
        )
        result = filter_messages_for_snapshot([msg])
        att = result[0]["metadata"]["attachments"][0]  # type: ignore[index]
        assert att["filename"] == "report.pdf"
        assert att["mime_type"] == "application/pdf"
        assert att["size"] == 2048
        assert "url" not in att
        assert "content" not in att

    def test_excludes_system_messages(self) -> None:
        msgs = [
            {"role": "system", "content": [{"type": "text", "text": "You are..."}]},
            _user_msg("hello"),
        ]
        result = filter_messages_for_snapshot(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_strips_memory_snapshot(self) -> None:
        msg = _user_msg(
            "hello",
            memory_snapshot={"text": "User prefers dark mode", "ids": ["m1"]},
            relevance_snapshot={"score": 0.9},
        )
        result = filter_messages_for_snapshot([msg])
        meta = result[0]["metadata"]
        assert "memory_snapshot" not in meta
        assert "relevance_snapshot" not in meta

    def test_empty_list(self) -> None:
        assert filter_messages_for_snapshot([]) == []
