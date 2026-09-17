-- Read-only API boundary. Apply as the migration administrator, never as the API role.
-- No ingestion, scoring, network calls, publication mutation, or caller-controlled clock.
BEGIN;

-- Deliberately fail on a role-name collision rather than trust pre-existing privileges.
CREATE ROLE risk_api_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE risk_api_reader NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO risk_api_owner, risk_api_reader;
GRANT SELECT ON public.assessments, public.reserve_snapshots, public.evidence_records TO risk_api_owner;
CREATE POLICY risk_api_owner_read ON public.assessments FOR SELECT TO risk_api_owner USING (true);
CREATE POLICY risk_api_owner_read ON public.reserve_snapshots FOR SELECT TO risk_api_owner USING (true);
CREATE POLICY risk_api_owner_read ON public.evidence_records FOR SELECT TO risk_api_owner USING (true);

CREATE FUNCTION public.get_latest_risk_assessment(
    p_chain text DEFAULT 'base',
    p_protocol text DEFAULT 'aave-v3',
    p_asset text DEFAULT '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
    p_methodology text DEFAULT 'v0.1'
) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET timezone = 'UTC'
AS $function$
DECLARE
    a public.assessments%ROWTYPE;
    s public.reserve_snapshots%ROWTYPE;
    envelope jsonb;
    doc jsonb;
    factor jsonb;
    factors jsonb := '[]'::jsonb;
    names text[] := ARRAY['oracle_risk', 'liquidity_utilization_risk',
                         'collateral_configuration_risk', 'operational_restriction_risk'];
    seen text[] := ARRAY[]::text[];
    fresh_until timestamptz;
    expires_at timestamptz;
    unknown_after timestamptz;
    delivery_time timestamptz := statement_timestamp();
    freshness text;
    n bigint;
    result jsonb := jsonb_build_object(
        'assessment_id', NULL, 'chain', 'base', 'protocol', 'aave-v3',
        'asset', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', 'methodology_version', 'v0.1',
        'score', NULL, 'risk_level', 'unknown', 'confidence', NULL,
        'status', 'UNKNOWN', 'freshness_status', 'unknown', 'reason', 'NO_ASSESSMENT',
        'block_number', NULL, 'calculated_at', NULL, 'fresh_until', NULL,
        'expires_at', NULL, 'unknown_after', NULL, 'factors', '[]'::jsonb);
BEGIN
    IF (p_chain = 'base' AND p_protocol = 'aave-v3'
        AND lower(p_asset) = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
        AND p_methodology = 'v0.1') IS NOT TRUE THEN
        RETURN result || jsonb_build_object('reason', 'UNSUPPORTED_SUBJECT');
    END IF;

    -- Select before completeness checks: a newer partial/UNKNOWN write must not be
    -- hidden by an older favorable result. STABLE gives all reads one statement snapshot.
    SELECT * INTO a FROM public.assessments
    WHERE chain = 'base' AND protocol = 'aave-v3'
      AND asset = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913' AND methodology_version = 'v0.1'
    ORDER BY calculated_at DESC, created_at DESC, id DESC LIMIT 1;
    IF NOT FOUND THEN RETURN result; END IF;

    SELECT * INTO s FROM public.reserve_snapshots WHERE assessment_id = a.id;
    IF NOT FOUND THEN
        RETURN result || jsonb_build_object('reason', 'INCOMPLETE_PUBLICATION');
    END IF;
    SELECT count(*) INTO n FROM public.evidence_records
    WHERE assessment_id = a.id
      AND evidence_json->>'transformation' = 'serialize_precomputed_assessment';
    IF n <> 1 THEN
        RETURN result || jsonb_build_object('reason', 'INCOMPLETE_PUBLICATION');
    END IF;
    SELECT evidence_json INTO envelope FROM public.evidence_records
    WHERE assessment_id = a.id
      AND evidence_json->>'transformation' = 'serialize_precomputed_assessment';
    doc := (envelope->>'raw_result')::jsonb;

    -- Verify the writer's serialized assessment and its identities against stored rows.
    -- Snapshot content IDs are not necessarily the database snapshot UUID.
    IF (jsonb_typeof(doc) = 'object'
        AND envelope->>'id' = 'precomputed-assessment:' || a.id::text
        AND envelope->>'outcome' = 'success'
        AND envelope #>> '{source,section}' = 'precomputed_assessment'
        AND envelope #>> '{source,revision}' = 'v0.1'
        AND envelope->>'content_hash' = '0x' || encode(sha256(convert_to(envelope->>'raw_result', 'UTF8')), 'hex')
        AND doc->>'id' = a.id::text
        AND doc->>'methodology_version' = a.methodology_version
        AND doc->>'snapshot_id' = s.snapshot_json->>'id'
        AND doc->'subject' = s.snapshot_json->'subject'
        AND doc #>> '{subject,chain_id}' = '8453'
        AND doc #>> '{subject,protocol}' = a.protocol
        AND doc #>> '{subject,asset}' = a.asset
        AND doc #>> '{subject,pool}' = '0xa238dd80c259a72e81d7e4664a9801593f98d1c5'
        AND s.snapshot_json #>> '{block,number}' = a.block_number
        AND s.asset = a.asset AND s.block_number = a.block_number
        AND (doc->>'computed_at')::timestamptz = a.calculated_at
        AND (doc #>> '{freshness,evaluated_at}')::timestamptz = a.calculated_at
        AND doc->'score' IS NOT DISTINCT FROM coalesce(to_jsonb(a.score), 'null'::jsonb)
        AND doc->>'risk_level' = a.risk_level
        AND doc->'confidence' IS NOT DISTINCT FROM coalesce(to_jsonb(a.confidence), 'null'::jsonb)
        AND doc->>'freshness_status' = a.freshness_status
        AND lower(doc #>> '{freshness,status}') = a.freshness_status
        AND a.calculated_at <= delivery_time) IS NOT TRUE THEN
        RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
    END IF;

    -- The writer stores every snapshot evidence record plus the assessment envelope.
    -- Match original evidence IDs/content, not transformed database UUIDs or mere row counts.
    IF (jsonb_typeof(s.snapshot_json->'evidence') = 'array'
        AND jsonb_typeof(doc->'evidence_ids') = 'array') IS NOT TRUE THEN
        RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
    END IF;
    SELECT count(*) INTO n FROM jsonb_array_elements(s.snapshot_json->'evidence');
    IF n = 0 OR n <> (SELECT count(DISTINCT e->>'id') FROM jsonb_array_elements(s.snapshot_json->'evidence') e)
        OR n + 1 <> (SELECT count(*) FROM public.evidence_records WHERE assessment_id = a.id)
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(s.snapshot_json->'evidence') e
            WHERE NOT EXISTS (SELECT 1 FROM public.evidence_records r
                              WHERE r.assessment_id = a.id AND r.evidence_json = e))
        OR jsonb_array_length(doc->'evidence_ids') = 0
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(doc->'evidence_ids') ref
            WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(s.snapshot_json->'evidence') e
                              WHERE e->>'id' = ref)) THEN
        RETURN result || jsonb_build_object('reason', 'INCOMPLETE_PUBLICATION');
    END IF;

    -- Four known factors only. Explicit field projection prevents raw evidence/extra
    -- JSON keys leaking; bounds reject oversized findings rather than truncate their meaning.
    IF jsonb_typeof(doc->'factors') IS DISTINCT FROM 'array' THEN
        RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
    END IF;
    IF jsonb_array_length(doc->'factors') <> 4 THEN
        RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
    END IF;
    FOR factor IN SELECT value FROM jsonb_array_elements(doc->'factors') LOOP
        IF (factor->>'factor' = ANY(names) AND NOT factor->>'factor' = ANY(seen)
            AND factor->>'status' IN ('evaluated', 'unknown')
            AND jsonb_typeof(factor->'explanation') = 'string'
            AND length(factor->>'explanation') BETWEEN 1 AND 2000
            AND jsonb_typeof(factor->'rule_id') = 'string'
            AND length(factor->>'rule_id') BETWEEN 1 AND 128
            AND ((factor->>'status' = 'evaluated' AND jsonb_typeof(factor->'score') = 'number'
                  AND (factor->>'score')::numeric BETWEEN 0 AND 100)
                 OR (factor->>'status' = 'unknown' AND factor->'score' = 'null'::jsonb AND a.score IS NULL))
            AND jsonb_typeof(factor->'evidence_ids') = 'array') IS NOT TRUE THEN
            RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(factor->'evidence_ids') ref
                   WHERE NOT (doc->'evidence_ids' ? ref)) THEN
            RETURN result || jsonb_build_object('reason', 'INCOMPLETE_PUBLICATION');
        END IF;
        seen := array_append(seen, factor->>'factor');
        factors := factors || jsonb_build_array(jsonb_build_object(
            'factor', factor->>'factor', 'status', factor->>'status', 'score', factor->'score',
            'explanation', factor->>'explanation', 'rule_id', factor->>'rule_id'));
    END LOOP;

    fresh_until := (doc #>> '{freshness,fresh_until}')::timestamptz;
    expires_at := (doc #>> '{freshness,expires_at}')::timestamptz;
    unknown_after := (doc #>> '{freshness,unknown_after}')::timestamptz;
    IF expires_at IS DISTINCT FROM a.stale_after
        OR (a.freshness_status = 'unknown' AND (fresh_until IS NOT NULL OR expires_at IS NOT NULL))
        OR (a.freshness_status <> 'unknown' AND (
            fresh_until IS NULL OR expires_at IS NULL OR NOT isfinite(fresh_until) OR NOT isfinite(expires_at)
            OR fresh_until > expires_at
            OR (a.freshness_status = 'fresh' AND a.calculated_at >= fresh_until)
            OR (a.freshness_status = 'degraded' AND a.calculated_at >= expires_at)
            OR (a.freshness_status = 'stale' AND a.calculated_at < expires_at)))
        OR (unknown_after IS NOT NULL AND (NOT isfinite(unknown_after) OR expires_at IS NULL
            OR unknown_after < expires_at OR a.calculated_at >= unknown_after)) THEN
        RETURN result || jsonb_build_object('reason', 'INVALID_PUBLICATION');
    END IF;
    freshness := CASE
        WHEN a.freshness_status = 'unknown' OR delivery_time >= unknown_after THEN 'unknown'
        WHEN a.freshness_status = 'stale' OR delivery_time >= expires_at THEN 'stale'
        WHEN a.freshness_status = 'degraded' OR delivery_time >= fresh_until THEN 'degraded'
        ELSE 'fresh' END;
    result := result || jsonb_build_object(
        'assessment_id', a.id, 'block_number', a.block_number, 'calculated_at', a.calculated_at,
        'fresh_until', fresh_until, 'expires_at', expires_at, 'unknown_after', unknown_after,
        'freshness_status', freshness);
    IF freshness IN ('stale', 'unknown') THEN
        RETURN result || jsonb_build_object('status', upper(freshness), 'reason',
            CASE WHEN a.freshness_status = 'unknown' THEN 'ASSESSMENT_UNKNOWN' ELSE 'EXPIRED_ASSESSMENT' END);
    ELSIF a.score IS NULL THEN
        RETURN result || jsonb_build_object('reason', 'ASSESSMENT_UNKNOWN');
    END IF;
    RETURN result || jsonb_build_object(
        'status', CASE freshness WHEN 'fresh' THEN 'OK' ELSE 'DEGRADED' END, 'reason', NULL,
        'score', a.score, 'risk_level', a.risk_level, 'confidence', a.confidence, 'factors', factors);
EXCEPTION WHEN data_exception THEN
    -- Bad persisted JSON/timestamps must not expose raw values through cast errors.
    RETURN result || jsonb_build_object('status', 'UNKNOWN', 'freshness_status', 'unknown',
        'reason', 'INVALID_PUBLICATION', 'score', NULL, 'confidence', NULL,
        'risk_level', 'unknown', 'factors', '[]'::jsonb);
END;
$function$;

REVOKE ALL ON FUNCTION public.get_latest_risk_assessment(text, text, text, text)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.get_latest_risk_assessment(text, text, text, text) TO risk_api_reader;

COMMENT ON FUNCTION public.get_latest_risk_assessment(text, text, text, text) IS
    'Latest v0.1 Base native-USDC projection; newest incomplete result is unavailable, never older fallback. Confidence is as of calculated_at. No ingestion/scoring. Original deadlines only.';

-- Transfer ownership without leaving the API principal a member of the privileged owner.
-- CREATE is needed only for ownership transfer, not for executing the function.
DO $ownership$
BEGIN
    EXECUTE format('GRANT risk_api_owner TO %I', current_user);
END;
$ownership$;
GRANT CREATE ON SCHEMA public TO risk_api_owner;
ALTER FUNCTION public.get_latest_risk_assessment(text, text, text, text) OWNER TO risk_api_owner;
REVOKE CREATE ON SCHEMA public FROM risk_api_owner;
DO $ownership$
BEGIN
    EXECUTE format('REVOKE risk_api_owner FROM %I', current_user);
END;
$ownership$;

-- PostgREST can assume only the read principal with a separately provisioned trusted JWT.
-- This does not issue a credential or grant anon/authenticated access.
GRANT risk_api_reader TO authenticator;

COMMIT;
