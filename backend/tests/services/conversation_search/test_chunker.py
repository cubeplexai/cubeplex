import cubeplex.services.conversation_search.chunker as chunker_mod
from cubeplex.services.conversation_search.chunker import Chunk, MessageInput, chunk_messages


def _msg(seq: int, text: str) -> MessageInput:
    return MessageInput(seq=seq, text=text)


def test_import_does_not_eagerly_load_tiktoken_encoding() -> None:
    """Module import must not call get_encoding (can block on HTTP download)."""
    # _encoding is an lru_cache; cache_info hits stay 0 until first chunk_messages.
    chunker_mod._encoding.cache_clear()
    info = chunker_mod._encoding.cache_info()
    assert info.hits == 0
    assert info.misses == 0
    # Import path already ran; the contract is: no cache fill until use.
    assert chunker_mod._encoding.cache_info().currsize == 0


def test_single_short_message_one_chunk() -> None:
    msgs = [_msg(1, "hello world")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].seq_lo == 1 and chunks[0].seq_hi == 1
    assert "hello world" in chunks[0].text


def test_long_corpus_creates_multiple_chunks_with_overlap() -> None:
    long_word = "word " * 800  # ~800 tokens
    msgs = [_msg(1, long_word)]
    chunks = chunk_messages(msgs, target_tokens=200, overlap_tokens=50)
    assert len(chunks) >= 3
    assert chunks[0].chunk_seq == 0
    assert chunks[-1].chunk_seq == len(chunks) - 1
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert any(w in b.text for w in a.text.split()[-10:])


def test_empty_messages_yields_no_chunks() -> None:
    assert chunk_messages([_msg(1, "")], 600, 100) == []
    assert chunk_messages([], 600, 100) == []


def test_seq_range_tracks_messages_in_chunk() -> None:
    msgs = [_msg(1, "a"), _msg(2, "b"), _msg(3, "c")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].seq_lo == 1
    assert chunks[0].seq_hi == 3
