"""Shared test fixtures."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def tenant_domain():
    return "testaviva.dk"


@pytest.fixture
def mock_user_cache():
    """Mock UserCache that returns minimal user data for lookups."""
    cache = MagicMock()
    cache.get.return_value = {
        "id": "user-1",
        "email": "user@test.dk",
        "displayName": "Test User",
        "userType": "Member",
        "identities": [],
    }
    return cache
