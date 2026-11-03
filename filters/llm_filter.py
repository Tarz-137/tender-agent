import re

from pydantic import BaseModel,Field
from langchain_ollama import ChatOllama

from capabilities import RIPPLES_CAPABILITIES
from prompts.relevance_examples import FEW_SHOT_EXAMPLES
from metrics_tracker import metrics_tracker

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
)
class RelevanceResult(BaseModel):
    score:float = Field(
        ge = 0,
        le = 1
    )

    relevant:bool
    reason: str

llm = ChatOllama(
    model = OLLAMA_CHAT_MODEL,
    temperature = 0,
    base_url = OLLAMA_BASE_URL,
    reasoning = False,
)

structured_llm = llm.with_structured_output(
    RelevanceResult,
    method="json_schema",
    include_raw=True,
)


def record_token_usage(raw_message) -> None:
    usage = getattr(raw_message, "usage_metadata", None) or {}
    response_metadata = (
        getattr(raw_message, "response_metadata", None) or {}
    )

    input_tokens = int(
        usage.get(
            "input_tokens",
            response_metadata.get("prompt_eval_count", 0),
        )
        or 0
    )
    output_tokens = int(
        usage.get(
            "output_tokens",
            response_metadata.get("eval_count", 0),
        )
        or 0
    )
    total_tokens = int(
        usage.get(
            "total_tokens",
            input_tokens + output_tokens,
        )
        or 0
    )

    metrics_tracker.add_tokens(
        "llm",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def recover_structured_result(raw_content: str) -> RelevanceResult:
    """Recover JSON surrounded by reasoning text or Markdown fences."""

    cleaned = re.sub(
        r"<think>.*?</think>",
        "",
        raw_content,
        flags=re.DOTALL,
    ).strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

    try:
        return RelevanceResult.model_validate_json(cleaned)
    except ValueError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end <= start:
            raise

        return RelevanceResult.model_validate_json(
            cleaned[start:end + 1]
        )

def llm_relevance_filter(tender):
    prompt = f"""
You are a tender-relevance evaluator for Ripples Engineering, you are evaluating whether a government tender is commercially relevant
to Ripples Engineering.

RIPPLES ENGINEERING CAPABILITIES:

{RIPPLES_CAPABILITIES}

Score the tender based ONLY on overlap with these stated capabilities.

Important rules:
- Do not assume capabilities that are not listed.
- Generic water-related work is not automatically relevant
- Generic pumps are not automatically relevant unless connected to a stated Ripples Capability
- Generic eletrical,civil,landscaping or MEP work should recieve a high score only when the tender
  clearly contains a relevant fountain,water-feature,multimedia,projection,lighting-show,aeration or related scope
- If the tender contains a relevant subsystem as part of a larger project, score according to how substantial that relevant score 
  appears to be
- Tender value,organisation and location are contextual information only and should not independently increase relevance.

SCORE CALIBRATION:

0.00-0.20
No meaningful capability overlap.

0.21-0.40
Weak or adjacent overlap. A related component may exist, but the main scope is outside Ripples Engineering's capabilites. 

0.41 - 0.60
Moderate overlap. Relevant work is present, but forms only part of the scope or relevance is uncertain

0.61 - 0.80
Strong overlap. A substantial portion of the tender matches Ripples Engineering's capabilities. 

0.81-1.00
Direct core-business oppurtunity with clear and substantial capability

REFERENCE EXAMPLES:
{FEW_SHOT_EXAMPLES}

Now evaluate the following tender using the same scoring logic.

TENDER DETAILS:
-Title: {tender.title}
-Description: {tender.description}
-Organisation: {tender.organisation}
-Product Category: {tender.product_category}
-Location: {tender.location}
-Tender Value: {tender.tender_value}

Return only a JSON object matching the required schema.
Do not include Markdown, code fences or commentary.

"""
    response = structured_llm.invoke(
        prompt
    )

    raw_message = response["raw"]
    record_token_usage(raw_message)

    result = response.get("parsed")

    if result is None:
        raw_content = raw_message.content

        if not isinstance(raw_content, str):
            raise ValueError(
                "LLM returned non-text content that could not be parsed"
            )

        try:
            result = recover_structured_result(raw_content)
        except ValueError as exc:
            preview = raw_content[:500]
            raise ValueError(
                "Could not parse LLM response as RelevanceResult. "
                f"Raw response begins with: {preview!r}"
            ) from exc

    tender.llm_score  =  result.score
    tender.relevant = result.relevant
    return tender
