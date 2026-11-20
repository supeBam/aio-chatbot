# Flat requirement state and category confirmation

Status: resolved

## Goal

Update the Dify Chatflow to collect business category, flat interested/uninterested tags, and speed/price/contract constraints across turns. Describe BN, OTT, and unsupported MS in the extraction prompt. Require explicit user confirmation of the interpreted business category before emitting a backend query.

## Acceptance

- Preserve existing state when a turn does not mention a field.
- Do not organize tags or numeric values into BN/OTT/MS sub-objects.
- Keep ambiguous OTT bundle ownership as a clarification instead of guessing.
- Mark the category unconfirmed whenever it is first inferred or changed.
- Emit no backend query until the user explicitly confirms the proposed category.
- Treat MS as out of scope and tell the user so.

## Answer

Updated the Dify extraction prompt and reducer to maintain one state schema, keep scene `scan_tag` separate from positive `plan_tags` and `uninterested_plan_tags`, preserve unmentioned fields, migrate legacy states, and require an explicit later-turn category confirmation. The extraction node uses a structured-output schema and the reducer consumes the `structured_output` object. BN and OTT are supported; MS is explained as out of scope. The workflow returns a category confirmation question until confirmed and emits the complete query state only afterward.

## Comments

Regression replay found that a repeated category proposal combined with an implicit confirmation caused the reducer to reject the entire turn, including new numeric constraints. The reducer now treats unchanged proposals as no-ops, ignores non-explicit confirmation decisions, and retains the valid numeric/tag changes while keeping the category unconfirmed.
