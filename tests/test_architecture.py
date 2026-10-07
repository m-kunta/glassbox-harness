import ast
import importlib.util
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
    assert {"fastapi>=0.110", "uvicorn>=0.27", "jinja2>=3.1", "python-multipart>=0.0.9"} <= set(
        config["project"]["dependencies"]
    )
    assert set(config["project"]["optional-dependencies"]["dev"]) >= {
        "httpx>=0.27",
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
        "web-dependencies": {
            "source_modules": ["glassbox.web"],
            "forbidden_modules": [
                "glassbox.eval.judge",
                "glassbox.eval.judge_config",
                "glassbox.eval.judge_provider",
                "glassbox.eval.judge_models",
                "glassbox.eval.reconciliation",
                "glassbox.sdk",
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
    assert len(user_options.contracts_options) == 6

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


def test_package_metadata_declares_judge_extras() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    optional = config["project"]["optional-dependencies"]

    assert optional["judge"] == ["python-dotenv>=1.0"]
    assert optional["judge-claude"] == ["python-dotenv>=1.0", "anthropic>=0.45"]
    assert optional["judge-openai"] == ["python-dotenv>=1.0", "openai>=1.0"]
    assert optional["judge-gemini"] == ["python-dotenv>=1.0", "google-genai>=1.0"]


def test_gitignore_excludes_the_judge_env_secrets_file() -> None:
    entries = (PROJECT_ROOT / ".gitignore").read_text().splitlines()

    assert ".env" in entries


def _module_level_import_names(source_path: Path, *, package: str | None = None) -> set[str]:
    """Inspect eager module/class blocks; resolve relative imports when scoped."""
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    names: set[str] = set()
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if node.level:
                if package is None:
                    continue
                module = importlib.util.resolve_name("." * node.level + (module or ""), package)
            if module:
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
        pending.extend(ast.iter_child_nodes(node))
    return names


def _all_absolute_import_names(source_root: Path) -> set[str]:
    """Return absolute imports made anywhere below one package directory."""
    names: set[str] = set()
    for source_path in source_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
    return names


def test_web_uses_only_the_single_public_drift_module(tmp_path: Path) -> None:
    imports = _all_absolute_import_names(PROJECT_ROOT / "glassbox" / "web")
    drift_imports = {name for name in imports if name.startswith("glassbox.eval.drift")}

    assert drift_imports <= {"glassbox.eval.drift"}

    mutation = tmp_path / "web" / "bad_import.py"
    mutation.parent.mkdir()
    mutation.write_text("from glassbox.eval.drift_policy import load_policy\n")
    mutated_imports = _all_absolute_import_names(tmp_path / "web")
    mutated_drift_imports = {
        name for name in mutated_imports if name.startswith("glassbox.eval.drift")
    }
    assert not mutated_drift_imports <= {"glassbox.eval.drift"}


def test_cli_keeps_drift_imports_inside_the_drift_dispatch_branch() -> None:
    imports = _module_level_import_names(PROJECT_ROOT / "glassbox" / "cli.py")

    assert "glassbox.eval.drift" not in imports


def _assert_reconciliation_imports_are_lazy(source_path: Path) -> None:
    imports = _module_level_import_names(source_path, package="glassbox")
    assert not any(
        name == "glassbox.eval.reconciliation" or name.startswith("glassbox.eval.reconciliation.")
        for name in imports
    ), "reconciliation must be imported inside the outcomes dispatch branch"


def test_cli_keeps_reconciliation_imports_inside_the_outcomes_dispatch_branch() -> None:
    _assert_reconciliation_imports_are_lazy(PROJECT_ROOT / "glassbox" / "cli.py")


@pytest.mark.parametrize(
    "source",
    [
        "import glassbox.eval.reconciliation\n",
        "from glassbox.eval.reconciliation import import_outcomes\n",
        "from glassbox.eval import reconciliation\n",
        "from glassbox.eval import reconciliation as outcomes\n",
        "try:\n    import glassbox.eval.reconciliation\nexcept ImportError:\n    pass\n",
        "if True:\n    from glassbox.eval.reconciliation import import_outcomes\n",
        "if True:\n    from glassbox.eval import reconciliation as outcomes\n",
        "class CLI:\n    from glassbox.eval.reconciliation import import_outcomes\n",
        "class CLI:\n    class Outcomes:\n        from glassbox.eval import reconciliation\n",
        "from .eval.reconciliation import import_outcomes\n",
        "from .eval import reconciliation\n",
        "try:\n    from .eval.reconciliation import import_outcomes\nexcept ImportError:\n"
        "    pass\n",
        "if True:\n    from .eval import reconciliation as outcomes\n",
    ],
)
def test_lazy_reconciliation_check_rejects_module_level_imports(
    tmp_path: Path, source: str
) -> None:
    source_path = tmp_path / "cli.py"
    source_path.write_text(source)

    with pytest.raises(AssertionError, match="reconciliation must be imported"):
        _assert_reconciliation_imports_are_lazy(source_path)


@pytest.mark.parametrize("scope", ["def main(command)", "async def main(command)"])
def test_lazy_reconciliation_check_excludes_function_bodies(
    tmp_path: Path, scope: str
) -> None:
    source_path = tmp_path / "cli.py"
    source_path.write_text(
        f"{scope}:\n"
        "    if command == 'outcomes':\n"
        "        from glassbox.eval import reconciliation as outcomes\n"
        "        from glassbox.eval.reconciliation import import_outcomes\n"
        "        from .eval import reconciliation\n"
        "        from .eval.reconciliation import run_reconciliation_report\n"
        "        result = outcomes, import_outcomes\n"
    )

    _assert_reconciliation_imports_are_lazy(source_path)


@pytest.mark.parametrize("scope", ["def run(self)", "async def run(self)"])
def test_lazy_reconciliation_check_allows_class_method_imports(
    tmp_path: Path, scope: str
) -> None:
    source_path = tmp_path / "cli.py"
    source_path.write_text(
        "class CLI:\n"
        f"    {scope}:\n"
        "        from glassbox.eval import reconciliation\n"
        "        from .eval.reconciliation import import_outcomes\n"
    )

    _assert_reconciliation_imports_are_lazy(source_path)


def test_import_linter_forbids_sdk_collector_and_web_from_reconciliation() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    contracts = {
        contract["name"]: contract for contract in config["tool"]["importlinter"]["contracts"]
    }

    for name in ("sdk-dependencies", "collector-dependencies", "web-dependencies"):
        forbidden = contracts[name]["forbidden_modules"]
        assert any(
            module == "glassbox.eval.reconciliation"
            or "glassbox.eval.reconciliation".startswith(module + ".")
            for module in forbidden
        ), f"{name} must forbid glassbox.eval.reconciliation or a parent package"


def test_import_linter_forbids_sdk_collector_and_web_from_the_judge_module() -> None:
    """`glassbox.eval.judge` runs the P3a calibrated judge and must never be
    reachable from the tracing SDK, the collector, or the web app -- those
    packages instrument or serve production agent code and must not carry
    the judge stack's optional provider-SDK/egress surface as a transitive
    dependency. `sdk-dependencies` and `collector-dependencies` already
    forbid those two source modules from importing all of `glassbox.eval`
    (a strict superset of `glassbox.eval.judge`); `web-dependencies` is
    extended by this task to forbid `glassbox.eval.judge` specifically,
    since `glassbox.web` may have legitimate reasons to import other
    `glassbox.eval` submodules later."""
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contracts"]
    by_source: dict[str, list[str]] = {}
    for contract in contracts:
        for source in contract["source_modules"]:
            by_source.setdefault(source, []).extend(contract["forbidden_modules"])

    for source in ("glassbox.sdk", "glassbox.collector", "glassbox.web"):
        forbidden = by_source[source]
        assert "glassbox.eval.judge" in forbidden or "glassbox.eval" in forbidden, (
            f"{source} must be forbidden from importing glassbox.eval.judge"
        )


def test_import_linter_forbids_web_from_every_judge_sibling_module() -> None:
    """The Global Constraint is "web must not import judge code" broadly, not
    just `glassbox.eval.judge` itself. `judge_config` (loads `dotenv`),
    `judge_provider` (the provider-SDK factories), and `judge_models` (the
    eval-side re-export of the judge value types) are siblings that carry the
    same optional-dependency/egress surface, and import-linter's "forbidden"
    contract type does not automatically extend a restriction to sibling
    modules -- each must be listed explicitly in `web-dependencies` for
    `lint-imports` to actually catch a new import of one of them from
    `glassbox.web`."""
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contracts"]
    web_contract = next(
        contract for contract in contracts if contract["source_modules"] == ["glassbox.web"]
    )
    forbidden = set(web_contract["forbidden_modules"])

    for judge_module in (
        "glassbox.eval.judge",
        "glassbox.eval.judge_config",
        "glassbox.eval.judge_provider",
        "glassbox.eval.judge_models",
    ):
        assert judge_module in forbidden, (
            f"glassbox.web must be forbidden from importing {judge_module}"
        )


def test_judge_provider_keeps_optional_provider_sdk_imports_lazy() -> None:
    """Each optional judge SDK must be imported only inside its own factory
    function, never at module import time, so glassbox.eval.judge_provider
    stays importable without anthropic/openai/google-genai installed."""
    source_path = PROJECT_ROOT / "glassbox" / "eval" / "judge_provider.py"
    module_level_imports = _module_level_import_names(source_path)

    forbidden = {"anthropic", "openai", "google", "google.genai"}
    assert module_level_imports & forbidden == set()
