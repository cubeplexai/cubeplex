from tests.e2e.sharding import shard_for_node_id


def test_node_ids_are_stably_partitioned_without_gaps_or_duplicates() -> None:
    node_ids = [f"tests/e2e/test_example.py::test_case_{index}" for index in range(200)]
    shard_total = 4

    partitions = [
        {node_id for node_id in node_ids if shard_for_node_id(node_id, shard_total) == shard_index}
        for shard_index in range(shard_total)
    ]

    assert set().union(*partitions) == set(node_ids)
    assert sum(len(partition) for partition in partitions) == len(node_ids)
    assert all(partition for partition in partitions)


def test_node_id_assignment_is_repeatable() -> None:
    node_id = "tests/e2e/test_auth.py::test_login"

    assert shard_for_node_id(node_id, 4) == shard_for_node_id(node_id, 4)
