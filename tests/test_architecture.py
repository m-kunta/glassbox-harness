import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def _absolute_glassbox_import_violations(events_root: Path) -> list[str]:
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

    return violations


def test_package_metadata_declares_required_quality_tools() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert config["build-system"]["build-backend"] == "hatchling.build"
    assert set(config["project"]["dependencies"]) >= {"pydantic>=2.0"}
    assert set(config["project"]["optional-dependencies"]["dev"]) >= {
        "import-linter>=2.0",
        "mypy>=1.0",
        "pytest>=8.0",
        "ruff>=0.6",
    }


def test_import_linter_contracts_preserve_module_boundaries() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contract"]

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
        "collector-dependencies": {
            "source_modules": ["glassbox.collector"],
            "forbidden_modules": [
                "glassbox.eval",
                "glassbox.explain",
                "glassbox.sdk",
                "glassbox.web",
            ],
        },
        "store-dependencies": {
            "source_modules": ["glassbox.store"],
            "forbidden_modules": ["glassbox.eval", "glassbox.explain", "glassbox.web"],
        },
        "web-dependencies": {
            "source_modules": ["glassbox.web"],
            "forbidden_modules": ["glassbox.sdk"],
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


def test_events_source_uses_no_absolute_glassbox_imports() -> None:
    """Keep the dependency-neutral events package independently importable."""
    events_root = PROJECT_ROOT / "glassbox" / "events"

    assert _absolute_glassbox_import_violations(events_root) == []


def test_absolute_import_check_covers_nested_events_modules(tmp_path: Path) -> None:
    nested_module = tmp_path / "events" / "nested" / "module.py"
    nested_module.parent.mkdir(parents=True)
    nested_module.write_text("from glassbox.events import TraceEvent\n")

    assert _absolute_glassbox_import_violations(tmp_path / "events") == [
        f"{nested_module}: from glassbox.events import ..."
    ]
