.PHONY: typecheck test

typecheck:
	uv run mypy src/mail_ai_agent/

test:
	uv run python -m pytest tests/
