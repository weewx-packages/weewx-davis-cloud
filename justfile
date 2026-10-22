python := ".venv/bin/python"

fast:
    .venv/bin/ruff check bin install.py tests tools
    {{python}} -m mypy bin install.py tools
    {{python}} -m pytest -q

test:
    {{python}} -m pytest -q --cov=user.daviscloud --cov-report=term-missing

oracles:
    {{python}} tools/oracle_probe.py --download --output verification/oracles.json
