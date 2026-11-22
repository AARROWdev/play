import pytest

from play import library


@pytest.fixture(autouse=True)
def no_settle_wait(monkeypatch):
    """Test files are seconds old; PLAY normally waits for a new file to settle. Tests that check that set it back."""
    monkeypatch.setattr(library, 'SETTLE', 0.0)
