# Glassbox

Glassbox is a local-first Python tracing harness for supply-chain agent decisions.

The initial package defines dependency-neutral, immutable canonical event contracts.
Subsequent P0 tasks add local persistence and the public tracing SDK.

## Development

Use Python 3.11 or newer, then install the development extras and run the checks:

```shell
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy glassbox
lint-imports
```
