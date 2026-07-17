import pytest

from cubeplex.skills.sources.base import (
    CandidateIdError,
    decode_candidate_id,
    encode_candidate_id,
)


def test_roundtrip_with_slashes_and_source_id():
    cid = encode_candidate_id(
        "remote", "vercel-labs/skills/tree/main/skills/find-skills", source_id="sksrc-7"
    )
    assert "/" not in cid  # URL-path safe
    kind, source_id, ref = decode_candidate_id(cid)
    assert kind == "remote"
    assert source_id == "sksrc-7"
    assert ref == "vercel-labs/skills/tree/main/skills/find-skills"


def test_roundtrip_local_has_empty_source_id():
    cid = encode_candidate_id("local", "skl-ABC123")
    assert decode_candidate_id(cid) == ("local", "", "skl-ABC123")


def test_decode_rejects_garbage():
    with pytest.raises(CandidateIdError):
        decode_candidate_id("!!!not-base64!!!")
