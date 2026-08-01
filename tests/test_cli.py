import pytest

from grocea.cli import reset_database


def test_reset_requires_explicit_confirmation() -> None:
    with pytest.raises(SystemExit, match="Refusing reset without --yes"):
        reset_database(False)
