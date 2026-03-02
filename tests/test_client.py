"""Unit tests for Mythic API client."""

import pytest
from unittest.mock import Mock, patch

from mythic_cli.client import MythicClient, MythicAPIException
from mythic_cli.config import MythicConfig


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return MythicConfig(
        server_url="http://test.mythic.local:7443",
        api_key="test-token",
        verify_ssl=False,
        timeout=30
    )


@pytest.fixture
def client(mock_config):
    """Create a Mythic client with mock config."""
    return MythicClient(mock_config)


def test_client_initialization(client):
    """Test client initialization."""
    assert client.base_url == "http://test.mythic.local:7443"
    assert client.api_token == "test-token"
    assert client.config.verify_ssl is False


def test_get_headers_with_token(client):
    """Test header generation with API token."""
    headers = client._get_headers()
    assert headers["Content-Type"] == "application/json"
    assert headers["apitoken"] == "test-token"


def test_get_headers_without_token(mock_config):
    """Test header generation without API token."""
    mock_config.api_key = None
    client = MythicClient(mock_config)
    headers = client._get_headers()
    assert "apitoken" not in headers
    assert headers["Content-Type"] == "application/json"


@patch('httpx.Client.post')
def test_login_success(mock_post, mock_config):
    """Test successful login."""
    mock_config.api_key = None
    client = MythicClient(mock_config)
    
    # Mock successful response
    mock_response = Mock()
    mock_response.json.return_value = {
        "status": "success",
        "access_token": "new-token-123"
    }
    mock_response.raise_for_status = Mock()
    mock_post.return_value = mock_response
    
    result = client.login("admin", "password123")
    
    assert result is True
    assert client.api_token == "new-token-123"
    mock_post.assert_called_once()


@patch('httpx.Client.post')
def test_login_failure(mock_post, mock_config):
    """Test login failure."""
    mock_config.api_key = None
    client = MythicClient(mock_config)
    
    # Mock failed response
    mock_response = Mock()
    mock_response.json.return_value = {
        "status": "error",
        "error": "Invalid credentials"
    }
    mock_response.raise_for_status = Mock()
    mock_post.return_value = mock_response
    
    with pytest.raises(MythicAPIException, match="Login failed"):
        client.login("admin", "wrongpassword")
