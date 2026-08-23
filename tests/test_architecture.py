import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


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
