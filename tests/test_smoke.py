"""Smoke tests to verify basic project setup."""


def test_project_imports() -> None:
    """Verify the src package is importable."""
    import src  # noqa: F401
