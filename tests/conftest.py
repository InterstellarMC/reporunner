"""Pytest configuration and custom marker registration."""

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as slow integration tests (deselect with '-m \"not integration\"')",
    )
