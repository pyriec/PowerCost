"""Test configuration and fixtures for electricity_cost."""

import pytest

# Ensure custom components can be loaded by Home Assistant test suite
pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield
