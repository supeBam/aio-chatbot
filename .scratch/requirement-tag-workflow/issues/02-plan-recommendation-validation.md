# Plan recommendation and code validation

Status: resolved

## Goal

Use the backend-provided plan summary JSON as the sole recommendation candidate set. Have the recommendation LLM select up to three plans based on multi-turn user needs, then validate each returned code exactly against the candidates.

## Acceptance

- Return at most three distinct recommendations.
- Reject missing, altered, or duplicate `plan_code` values.
- Preserve the candidate's exact code spelling and value.
- Do not change or reproduce backend filtering logic in Dify.

## Answer

Updated the recommendation prompt to use the multi-turn state and backend candidate plans. The recommendation node now uses a structured-output schema and the Code node consumes its `structured_output` object. It accepts at most three distinct results and rejects missing, altered, or non-candidate codes without normalization. Added regression tests for successful recommendations and rejection cases.

## Comments
