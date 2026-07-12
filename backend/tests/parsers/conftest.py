"""Parser-registry conftest: make sure every test gets a freshly-discovered registry."""

from collections.abc import AsyncIterator

import pytest

from cubeplex.parsers import get_parser_registry, reset_parser_registry_for_tests


@pytest.fixture(autouse=True)
async def _bind_parser_registry() -> AsyncIterator[None]:
    reset_parser_registry_for_tests()
    await get_parser_registry().discover()
    yield
    reset_parser_registry_for_tests()
