from __future__ import annotations


def test_prompt_template_instructs_model_to_start_with_json() -> None:
    from mail_ai_agent.llm_gateway import PROMPT_TEMPLATE

    # The zasady section must contain instruction to start with {
    zasady_section = PROMPT_TEMPLATE.split("Zasady:")[1].split("Dozwolone")[0]
    assert "{" in zasady_section and "Zacznij" in zasady_section
