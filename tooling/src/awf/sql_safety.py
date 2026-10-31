"""Validate the exact PostgreSQL schema assigned to a practice number."""


def practice_schema(number: int) -> str:
    if type(number) is not int or number < 1 or number > 999_999:
        raise ValueError("Practice number must be a positive integer <= 999999")
    return f"practice_{number:03d}"


def require_practice_target(number: int, schema: str) -> None:
    """The configured destination must match THIS practice, not any practice_* schema."""
    expected = practice_schema(number)
    if schema != expected:
        raise ValueError(f"Unsafe SQL target: expected {expected}, received {schema!r}")
