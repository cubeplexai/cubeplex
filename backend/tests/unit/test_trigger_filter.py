"""Tests for declarative AND/OR + JSONPath filter matcher."""

import pytest

from cubeplex.triggers.filter import matches


class TestFilterMatcher:
    """Test filter matching logic with parametrized cases."""

    @pytest.mark.parametrize(
        "filter_tree,payload,expected",
        [
            # None filter matches everything
            (None, {}, True),
            (None, {"a": 1}, True),
            (None, {"event": {"action": "opened"}}, True),
            # eq operator
            (
                {"path": "event.action", "op": "eq", "value": "opened"},
                {"event": {"action": "opened"}},
                True,
            ),
            (
                {"path": "event.action", "op": "eq", "value": "opened"},
                {"event": {"action": "closed"}},
                False,
            ),
            # neq operator
            (
                {"path": "event.action", "op": "neq", "value": "opened"},
                {"event": {"action": "closed"}},
                True,
            ),
            (
                {"path": "event.action", "op": "neq", "value": "opened"},
                {"event": {"action": "opened"}},
                False,
            ),
            # neq with missing path (should be True)
            (
                {"path": "event.action", "op": "neq", "value": "opened"},
                {},
                True,
            ),
            # contains on string
            (
                {"path": "event.title", "op": "contains", "value": "bug"},
                {"event": {"title": "Found a bug"}},
                True,
            ),
            (
                {"path": "event.title", "op": "contains", "value": "bug"},
                {"event": {"title": "feature request"}},
                False,
            ),
            # contains on list
            (
                {"path": "labels", "op": "contains", "value": "p0"},
                {"labels": ["p0", "infra"]},
                True,
            ),
            (
                {"path": "labels", "op": "contains", "value": "p1"},
                {"labels": ["p0", "infra"]},
                False,
            ),
            # exists on missing path
            (
                {"path": "event.action", "op": "exists"},
                {},
                False,
            ),
            # exists on present path
            (
                {"path": "event.action", "op": "exists"},
                {"event": {"action": "opened"}},
                True,
            ),
            # in operator
            (
                {"path": "x", "op": "in", "value": [1, 2, 3]},
                {"x": 2},
                True,
            ),
            (
                {"path": "x", "op": "in", "value": [1, 2, 3]},
                {"x": 5},
                False,
            ),
            # in operator with missing path
            (
                {"path": "x", "op": "in", "value": [1, 2, 3]},
                {},
                False,
            ),
            # nested and: all children must match
            (
                {
                    "and": [
                        {"path": "event.action", "op": "eq", "value": "opened"},
                        {
                            "path": "event.title",
                            "op": "contains",
                            "value": "bug",
                        },
                    ]
                },
                {"event": {"action": "opened", "title": "Critical bug"}},
                True,
            ),
            (
                {
                    "and": [
                        {"path": "event.action", "op": "eq", "value": "opened"},
                        {
                            "path": "event.title",
                            "op": "contains",
                            "value": "bug",
                        },
                    ]
                },
                {"event": {"action": "opened", "title": "Feature"}},
                False,
            ),
            # nested or: any child must match
            (
                {
                    "or": [
                        {"path": "event.action", "op": "eq", "value": "opened"},
                        {"path": "event.action", "op": "eq", "value": "commented"},
                    ]
                },
                {"event": {"action": "opened"}},
                True,
            ),
            (
                {
                    "or": [
                        {"path": "event.action", "op": "eq", "value": "opened"},
                        {"path": "event.action", "op": "eq", "value": "commented"},
                    ]
                },
                {"event": {"action": "commented"}},
                True,
            ),
            (
                {
                    "or": [
                        {"path": "event.action", "op": "eq", "value": "opened"},
                        {"path": "event.action", "op": "eq", "value": "commented"},
                    ]
                },
                {"event": {"action": "closed"}},
                False,
            ),
            # mixed nested: and with or
            (
                {
                    "and": [
                        {
                            "or": [
                                {
                                    "path": "event.action",
                                    "op": "eq",
                                    "value": "opened",
                                },
                                {
                                    "path": "event.action",
                                    "op": "eq",
                                    "value": "commented",
                                },
                            ]
                        },
                        {
                            "path": "event.title",
                            "op": "contains",
                            "value": "bug",
                        },
                    ]
                },
                {"event": {"action": "opened", "title": "critical bug"}},
                True,
            ),
            (
                {
                    "and": [
                        {
                            "or": [
                                {
                                    "path": "event.action",
                                    "op": "eq",
                                    "value": "opened",
                                },
                                {
                                    "path": "event.action",
                                    "op": "eq",
                                    "value": "commented",
                                },
                            ]
                        },
                        {
                            "path": "event.title",
                            "op": "contains",
                            "value": "bug",
                        },
                    ]
                },
                {"event": {"action": "opened", "title": "feature"}},
                False,
            ),
        ],
    )
    def test_filter_matching(self, filter_tree, payload, expected):
        """Test filter matching with various trees and payloads."""
        assert matches(filter_tree, payload) is expected

    def test_unknown_op_raises(self):
        """Unknown op raises ValueError."""
        with pytest.raises(ValueError, match="unknown filter op"):
            matches(
                {
                    "path": "x",
                    "op": "starts_with",
                    "value": "y",
                },
                {"x": "yes"},
            )

    def test_empty_and_raises(self):
        """Empty and node raises ValueError."""
        with pytest.raises(ValueError, match="'and' node requires"):
            matches({"and": []}, {})

    def test_empty_or_raises(self):
        """Empty or node raises ValueError."""
        with pytest.raises(ValueError, match="'or' node requires"):
            matches({"or": []}, {})
