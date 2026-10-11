import pytest
import pytest_socket

pytest_socket.disable_socket = lambda *args, **kwargs: None
pytest_socket.enable_socket()

# Ensure custom components can be loaded by Home Assistant test suite
pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    pytest_socket.enable_socket()
    yield
