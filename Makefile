.PHONY: typecheck test ai-branch-sweep ai-branch-sweep-clean

typecheck:
	uv run mypy src/mail_ai_agent/

test:
	uv run python -m pytest tests/

ai-branch-sweep:
	bash scripts/ai_branch_sweep.sh

ai-branch-sweep-clean:
	bash scripts/ai_branch_sweep.sh --delete-duplicates
