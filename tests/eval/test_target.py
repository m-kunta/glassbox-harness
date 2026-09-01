import pytest

from glassbox.eval.target import load_target


def test_load_target_returns_imported_callable() -> None:
    target = load_target("tests.eval.targets:run_case")

    assert callable(target)


@pytest.mark.parametrize(
    "import_path",
    [
        "tests.eval.targets",
        "tests.eval.targets:missing_target",
        "tests.eval.targets:not_a_target",
    ],
)
def test_load_target_rejects_invalid_import_path(import_path: str) -> None:
    with pytest.raises(ValueError):
        load_target(import_path)
