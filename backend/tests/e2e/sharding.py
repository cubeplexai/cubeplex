import hashlib


def shard_for_node_id(node_id: str, shard_total: int) -> int:
    """Return the stable zero-based shard assigned to a pytest node ID."""
    if shard_total < 1:
        raise ValueError("shard_total must be at least 1")
    digest = hashlib.sha256(node_id.encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big") % shard_total
