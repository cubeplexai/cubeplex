from cubeplex.services.conversation_search.chunker import Chunk, MessageInput, chunk_messages
from cubeplex.utils.tokens import approx_tokens


def _msg(seq: int, text: str) -> MessageInput:
    return MessageInput(seq=seq, text=text)


def test_single_short_message_one_chunk() -> None:
    msgs = [_msg(1, "hello world")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].seq_lo == 1 and chunks[0].seq_hi == 1
    assert "hello world" in chunks[0].text


def test_long_corpus_creates_multiple_chunks_with_overlap() -> None:
    # Soft estimate ~0.3 tok/char for ASCII → need enough chars for ≥3 windows.
    long_word = "word " * 2000
    msgs = [_msg(1, long_word)]
    chunks = chunk_messages(msgs, target_tokens=200, overlap_tokens=50)
    assert len(chunks) >= 3
    assert chunks[0].chunk_seq == 0
    assert chunks[-1].chunk_seq == len(chunks) - 1
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert any(w in b.text for w in a.text.split()[-10:])


def test_chunk_soft_size_near_target() -> None:
    """Each non-final chunk should land near the soft target, not explode."""
    text = "hello world " * 500
    chunks = chunk_messages([_msg(1, text)], target_tokens=200, overlap_tokens=50)
    assert len(chunks) >= 2
    for c in chunks[:-1]:
        est = approx_tokens(c.text)
        # Soft window: allow generous band around target.
        assert 80 <= est <= 320, est


def test_empty_messages_yields_no_chunks() -> None:
    assert chunk_messages([_msg(1, "")], 600, 100) == []
    assert chunk_messages([], 600, 100) == []


def test_seq_range_tracks_messages_in_chunk() -> None:
    msgs = [_msg(1, "a"), _msg(2, "b"), _msg(3, "c")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].seq_lo == 1
    assert chunks[0].seq_hi == 3


def test_chinese_gets_smaller_char_windows_than_english() -> None:
    """Same soft target: CJK weight is higher → fewer chars per chunk."""
    en = "a" * 2000
    zh = "中" * 2000
    en_chunks = chunk_messages([_msg(1, en)], target_tokens=200, overlap_tokens=0)
    zh_chunks = chunk_messages([_msg(1, zh)], target_tokens=200, overlap_tokens=0)
    assert len(zh_chunks) > len(en_chunks)
    assert len(zh_chunks[0].text) < len(en_chunks[0].text)
