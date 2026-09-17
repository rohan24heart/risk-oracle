-- Confidence-only repair. Apply as an administrator authorized to replace the
-- risk_api_owner-owned function. No data, ownership, grants, or scoring changes.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

DO $migration$
DECLARE
    definition text := pg_get_functiondef(
        'public.get_latest_risk_assessment(text,text,text,text)'::regprocedure);
    old_comparison text := $old$doc->'confidence' IS NOT DISTINCT FROM coalesce(to_jsonb(a.confidence), 'null'::jsonb)$old$;
    new_comparison text := $new$(CASE WHEN jsonb_typeof(doc->'confidence') IN ('number', 'null')
            THEN (doc->>'confidence')::double precision IS NOT DISTINCT FROM a.confidence
            ELSE false END)$new$;
BEGIN
    -- Refuse unexpected deployed definitions rather than silently make a broader edit.
    IF (length(definition) - length(replace(definition, old_comparison, '')))
        <> length(old_comparison) THEN
        RAISE EXCEPTION 'Expected exactly one original confidence integrity comparison';
    END IF;
    -- pg_get_functiondef emits CREATE OR REPLACE. Replacing the function preserves
    -- its owner/ACL and every other check/configuration in the installed definition.
    -- The CASE retains JSON number/null typing: missing keys and strings still fail.
    EXECUTE replace(definition, old_comparison, new_comparison);
END;
$migration$;
COMMIT;
