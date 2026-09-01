import ast
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def _events_import_violations(events_root: Path) -> list[str]:
    violations: list[str] = []

    for source_path in events_root.rglob("*.py"):
        for node in ast.walk(ast.parse(source_path.read_text(), filename=str(source_path))):
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{source_path}: import {alias.name}"
                    for alias in node.names
                    if alias.name == "glassbox" or alias.name.startswith("glassbox.")
                )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "glassbox" or node.module.startswith("glassbox."):
                    violations.append(f"{source_path}: from {node.module} import ...")
            elif isinstance(node, ast.ImportFrom) and node.level - 1 > len(
                source_path.relative_to(events_root).parent.parts
            ):
                violations.append(f"{source_path}: relative import escapes glassbox.events")

    return violations


def test_package_metadata_declares_required_quality_tools() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert config["build-system"]["build-backend"] == "hatchling.build"
    assert set(config["project"]["dependencies"]) >= {"pydantic>=2.0"}
    assert "PyYAML>=6.0" in config["project"]["dependencies"]
    assert set(config["project"]["optional-dependencies"]["dev"]) >= {
        "import-linter>=2.0",
        "mypy>=1.0",
        "pytest>=8.0",
        "ruff>=0.6",
    }


def test_import_linter_contracts_preserve_module_boundaries() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contracts"]

    expected = {
        "events-dependency-neutral": {
            "source_modules": ["glassbox.events"],
            "forbidden_modules": [
                "glassbox.collector",
                "glassbox.eval",
                "glassbox.explain",
                "glassbox.sdk",
                "glassbox.store",
                "glassbox.web",
            ],
        },
        "store-dependencies": {
            "source_modules": ["glassbox.store"],
            "forbidden_modules": ["glassbox.eval", "glassbox.explain", "glassbox.web"],
        },
        "collector-dependencies": {
            "source_modules": ["glassbox.collector"],
            "forbidden_modules": [
                "glassbox.eval",
                "glassbox.explain",
                "glassbox.sdk",
                "glassbox.web",
            ],
        },
        "sdk-dependencies": {
            "source_modules": ["glassbox.sdk"],
            "forbidden_modules": [
                "glassbox.collector",
                "glassbox.eval",
                "glassbox.explain",
                "glassbox.store",
                "glassbox.web",
            ],
        },
        "eval-dependencies": {
            "source_modules": ["glassbox.eval"],
            "forbidden_modules": [
                "glassbox.collector",
                "glassbox.sdk",
                "glassbox.explain",
                "glassbox.web",
            ],
        },
    }

    actual = {
        contract["name"]: {
            "source_modules": contract["source_modules"],
            "forbidden_modules": contract["forbidden_modules"],
        }
        for contract in contracts
    }
    assert actual == expected


def test_import_linter_actually_evaluates_and_enforces_the_configured_contracts() -> None:
    """Regression guard: a wrong TOML array key (singular "contract" instead of
    "contracts") parses without error but leaves import-linter's contract list
    empty, so `lint-imports` silently checks nothing and always exits success.
    See TODO.md decision log, 2026-08-22."""
    from importlinter.application.use_cases import lint_imports, read_user_options
    from importlinter.configuration import configure

    configure()
    config_path = str(PROJECT_ROOT / "pyproject.toml")

    user_options = read_user_options(config_filename=config_path)
    assert len(user_options.contracts_options) == 5

    assert lint_imports(config_filename=config_path, cache_dir=None) is True


def test_events_source_uses_no_absolute_glassbox_imports() -> None:
    """Keep the dependency-neutral events package independently importable."""
    events_root = PROJECT_ROOT / "glassbox" / "events"

    assert _events_import_violations(events_root) == []


def test_absolute_import_check_covers_nested_events_modules(tmp_path: Path) -> None:
    nested_module = tmp_path / "events" / "nested" / "module.py"
    nested_module.parent.mkdir(parents=True)
    nested_module.write_text("from glassbox.events import TraceEvent\n")

    assert _events_import_violations(tmp_path / "events") == [
        f"{nested_module}: from glassbox.events import ..."
    ]


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (Path("module.py"), "from ..collector import Collector\n"),
        (Path("module.py"), "from .. import collector\n"),
        (Path("nested/module.py"), "from ...collector import Collector\n"),
        (Path("nested/module.py"), "from ... import collector\n"),
    ],
)
def test_events_import_check_rejects_relative_imports_that_escape_events(
    tmp_path: Path, relative_path: Path, source: str
) -> None:
    source_path = tmp_path / "events" / relative_path
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source)

    assert _events_import_violations(tmp_path / "events") == [
        f"{source_path}: relative import escapes glassbox.events"
    ]


def test_events_import_check_allows_nested_relative_imports_within_events(
    tmp_path: Path,
) -> None:
    nested_module = tmp_path / "events" / "nested" / "module.py"
    nested_module.parent.mkdir(parents=True)
    nested_module.write_text("from ..collector import Collector\n")

    assert _events_import_violations(tmp_path / "events") == []
