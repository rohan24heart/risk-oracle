-- Run only in an EMPTY, isolated database after all migrations, as its administrator:
-- psql -X -v ON_ERROR_STOP=1 -f supabase/tests/precomputed_serving.sql
-- No pgTAP dependency. Assertions raise on failure; all fixtures roll back.
BEGIN;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.assessments) THEN
        RAISE EXCEPTION 'Serving tests require an empty isolated database';
    END IF;
END $$;
CREATE TEMP TABLE serving_test_results (name text PRIMARY KEY);
CREATE FUNCTION pg_temp.check_result(test_name text, passed boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF passed IS NOT TRUE THEN RAISE EXCEPTION 'FAIL: %', test_name; END IF;
    INSERT INTO serving_test_results VALUES (test_name);
END $$;

-- Synthetic persisted writer shape. Original snapshot/evidence IDs intentionally
-- differ from their storage UUIDs, as they do in SupabaseWriter.
CREATE FUNCTION pg_temp.seed_assessment(
    sequence integer DEFAULT 1, age interval DEFAULT interval '1 minute',
    stored_status text DEFAULT 'fresh', unknown_score boolean DEFAULT false
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    aid uuid := ('00000000-0000-4000-8000-' || lpad(sequence::text, 12, '0'))::uuid;
    at_time timestamptz := statement_timestamp() - age;
    freshness jsonb;
    subject jsonb := jsonb_build_object('chain_id', 8453, 'protocol', 'aave-v3',
        'asset', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'pool', '0xa238dd80c259a72e81d7e4664a9801593f98d1c5');
    record jsonb := jsonb_build_object('id', 'evidence:one', 'outcome', 'success',
        'source', jsonb_build_object('kind', 'onchain_rpc'),
        'raw_result', 'PRIVATE_RAW_EVIDENCE');
    snapshot jsonb;
    doc jsonb;
    factor_name text;
    factors jsonb := '[]';
    score double precision := CASE WHEN unknown_score THEN NULL ELSE 20 END;
    risk text := CASE WHEN unknown_score THEN 'unknown' ELSE 'low' END;
    confidence double precision := CASE WHEN unknown_score THEN NULL ELSE 0.5 END;
BEGIN
    freshness := jsonb_build_object('status', upper(stored_status), 'evaluated_at', at_time,
        'fresh_until', CASE WHEN stored_status = 'unknown' THEN NULL ELSE at_time + interval '25 minutes' END,
        'expires_at', CASE WHEN stored_status = 'unknown' THEN NULL ELSE at_time + interval '45 minutes' END,
        'unknown_after', CASE WHEN stored_status = 'unknown' THEN NULL ELSE at_time + interval '90 minutes' END);
    snapshot := jsonb_build_object('id', 'snapshot:one', 'subject', subject,
        'block', jsonb_build_object('number', sequence::text), 'evidence', jsonb_build_array(record));
    FOREACH factor_name IN ARRAY ARRAY['oracle_risk', 'liquidity_utilization_risk',
        'collateral_configuration_risk', 'operational_restriction_risk'] LOOP
        factors := factors || jsonb_build_array(jsonb_build_object('factor', factor_name,
            'status', CASE WHEN unknown_score THEN 'unknown' ELSE 'evaluated' END,
            'score', score, 'explanation', 'Synthetic public finding', 'rule_id', 'v0.1:' || factor_name,
            'evidence_ids', jsonb_build_array('evidence:one'), 'private_extra', 'MUST_NOT_LEAK'));
    END LOOP;
    doc := jsonb_build_object('id', aid, 'methodology_version', 'v0.1', 'snapshot_id', 'snapshot:one',
        'subject', subject, 'computed_at', at_time, 'freshness', freshness, 'freshness_status', stored_status,
        'score', score, 'risk_level', risk, 'confidence', confidence, 'factors', factors,
        'evidence_ids', jsonb_build_array('evidence:one'), 'private_extra', 'MUST_NOT_LEAK');
    INSERT INTO public.assessments(id, chain, protocol, asset, score, risk_level, confidence,
        freshness_status, calculated_at, stale_after, block_number, methodology_version, created_at)
    VALUES (aid, 'base', 'aave-v3', subject->>'asset', score, risk, confidence,
        stored_status, at_time, (freshness->>'expires_at')::timestamptz, sequence::text, 'v0.1', at_time);
    INSERT INTO public.reserve_snapshots(assessment_id, asset, block_number, observed_at, snapshot_json)
    VALUES (aid, subject->>'asset', sequence::text, at_time, snapshot);
    INSERT INTO public.evidence_records(assessment_id, evidence_type, source, observed_at, evidence_json)
    VALUES (aid, 'onchain_rpc', 'synthetic', at_time, record),
           (aid, 'service_policy', 'service_policy', at_time, jsonb_build_object(
               'id', 'precomputed-assessment:' || aid, 'outcome', 'success',
               'transformation', 'serialize_precomputed_assessment',
               'source', jsonb_build_object('section', 'precomputed_assessment', 'revision', 'v0.1'),
               'raw_result', doc::text, 'content_hash', '0x' || encode(sha256(convert_to(doc::text, 'UTF8')), 'hex')));
    RETURN aid;
END $$;

-- Replace a serialized document while maintaining the writer's content hash.
CREATE FUNCTION pg_temp.replace_doc(aid uuid, doc jsonb) RETURNS void LANGUAGE sql AS $$
    UPDATE public.evidence_records SET evidence_json = evidence_json || jsonb_build_object(
        'raw_result', doc::text, 'content_hash', '0x' || encode(sha256(convert_to(doc::text, 'UTF8')), 'hex'))
    WHERE assessment_id = aid AND evidence_json->>'transformation' = 'serialize_precomputed_assessment';
$$;
CREATE FUNCTION pg_temp.read_doc(aid uuid) RETURNS jsonb LANGUAGE sql AS $$
    SELECT (evidence_json->>'raw_result')::jsonb FROM public.evidence_records
    WHERE assessment_id = aid AND evidence_json->>'transformation' = 'serialize_precomputed_assessment';
$$;

DO $tests$
DECLARE
    aid uuid;
    newer uuid;
    r jsonb;
    d jsonb;
    deadline timestamptz := statement_timestamp();
BEGIN
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('no assessment', r->>'reason' = 'NO_ASSESSMENT' AND r->'score' = 'null');
    aid := pg_temp.seed_assessment();
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('complete fresh assessment', r->>'status' = 'OK'
        AND r->>'assessment_id' = aid::text AND r->'score' = '20' AND r->'confidence' = '0.5'
        AND r->>'risk_level' = 'low' AND jsonb_array_length(r->'factors') = 4);
    PERFORM pg_temp.check_result('bounded public projection', (SELECT count(*) FROM jsonb_object_keys(r)) = 17
        AND r::text NOT LIKE '%PRIVATE_RAW_EVIDENCE%' AND r::text NOT LIKE '%MUST_NOT_LEAK%'
        AND NOT (r ? 'created_at') AND NOT (r ? 'evidence_ids')
        AND (SELECT count(*) FROM jsonb_object_keys(r #> '{factors,0}')) = 5);
    PERFORM pg_temp.check_result('checksum address accepted', public.get_latest_risk_assessment(
        'base', 'aave-v3', '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'v0.1')->>'status' = 'OK');
    PERFORM pg_temp.check_result('unsupported chain', public.get_latest_risk_assessment('ethereum')->>'reason' = 'UNSUPPORTED_SUBJECT');
    PERFORM pg_temp.check_result('unsupported protocol', public.get_latest_risk_assessment('base', 'other')->>'reason' = 'UNSUPPORTED_SUBJECT');
    PERFORM pg_temp.check_result('unsupported asset', public.get_latest_risk_assessment('base', 'aave-v3', 'USDC')->>'reason' = 'UNSUPPORTED_SUBJECT');
    PERFORM pg_temp.check_result('unsupported methodology', public.get_latest_risk_assessment(
        'base', 'aave-v3', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', 'v0.2')->>'reason' = 'UNSUPPORTED_SUBJECT');
    PERFORM pg_temp.check_result('null subject rejected', public.get_latest_risk_assessment(NULL)->>'reason' = 'UNSUPPORTED_SUBJECT');

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '50 minutes');
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('expired assessment masks all actionable fields', r->>'status' = 'STALE'
        AND r->'score' = 'null' AND r->'confidence' = 'null' AND r->>'risk_level' = 'unknown' AND r->'factors' = '[]'
        AND r->>'assessment_id' = aid::text);
    PERFORM pg_temp.check_result('expiry is not extended', (r->>'expires_at')::timestamptz = deadline - interval '5 minutes');
    d := pg_temp.read_doc(aid);
    d := jsonb_set(d, '{freshness,expires_at}', to_jsonb(deadline));
    PERFORM pg_temp.replace_doc(aid, d);
    UPDATE public.assessments SET stale_after = deadline WHERE id = aid;
    PERFORM pg_temp.check_result('exact expiry is stale', public.get_latest_risk_assessment()->>'status' = 'STALE');
    d := jsonb_set(d, '{freshness,expires_at}', to_jsonb(deadline + interval '1 microsecond'));
    PERFORM pg_temp.replace_doc(aid, d);
    UPDATE public.assessments SET stale_after = deadline + interval '1 microsecond' WHERE id = aid;
    PERFORM pg_temp.check_result('one microsecond before expiry remains degraded', public.get_latest_risk_assessment()->>'status' = 'DEGRADED');

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '25 minutes');
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('exact fresh deadline is degraded', r->>'status' = 'DEGRADED'
        AND r->'score' = '20' AND r->'confidence' = '0.5');
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '90 minutes');
    PERFORM pg_temp.check_result('exact unknown deadline', public.get_latest_risk_assessment()->>'status' = 'UNKNOWN'
        AND public.get_latest_risk_assessment()->'score' = 'null');

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment();
    DELETE FROM public.reserve_snapshots WHERE assessment_id = aid;
    PERFORM pg_temp.check_result('missing snapshot', public.get_latest_risk_assessment()->>'reason' = 'INCOMPLETE_PUBLICATION');
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment();
    DELETE FROM public.evidence_records WHERE assessment_id = aid AND evidence_type = 'onchain_rpc';
    PERFORM pg_temp.check_result('missing evidence', public.get_latest_risk_assessment()->>'reason' = 'INCOMPLETE_PUBLICATION');
    -- A different record with the same count must not satisfy completeness.
    INSERT INTO public.evidence_records(assessment_id, evidence_type, source, observed_at, evidence_json)
    VALUES (aid, 'onchain_rpc', 'synthetic', deadline, '{"id":"wrong-evidence"}');
    PERFORM pg_temp.check_result('wrong evidence despite matching row count', public.get_latest_risk_assessment()->>'reason' = 'INCOMPLETE_PUBLICATION');
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment();
    DELETE FROM public.evidence_records WHERE assessment_id = aid AND evidence_type = 'service_policy';
    PERFORM pg_temp.check_result('missing serialized assessment', public.get_latest_risk_assessment()->>'reason' = 'INCOMPLETE_PUBLICATION');

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '5 minutes');
    newer := pg_temp.seed_assessment(2, interval '1 minute');
    PERFORM pg_temp.check_result('newest complete row selected', public.get_latest_risk_assessment()->>'assessment_id' = newer::text);
    UPDATE public.assessments SET methodology_version = 'v0.2' WHERE id = newer;
    PERFORM pg_temp.check_result('other methodology does not shadow v0.1', public.get_latest_risk_assessment()->>'assessment_id' = aid::text);
    UPDATE public.assessments SET methodology_version = 'v0.1' WHERE id = newer;
    DELETE FROM public.reserve_snapshots WHERE assessment_id = newer;
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('newest partial blocks older favorable fallback', r->>'reason' = 'INCOMPLETE_PUBLICATION'
        AND r->'assessment_id' = 'null' AND r->'score' = 'null');

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '1 minute');
    newer := pg_temp.seed_assessment(2, interval '1 minute');
    PERFORM pg_temp.check_result('UUID breaks equal timestamp ties', public.get_latest_risk_assessment()->>'assessment_id' = newer::text);
    UPDATE public.assessments SET created_at = deadline WHERE id = aid;
    PERFORM pg_temp.check_result('creation time breaks calculation time ties', public.get_latest_risk_assessment()->>'assessment_id' = aid::text);

    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '5 minutes');
    newer := pg_temp.seed_assessment(2, interval '1 minute', 'fresh', true);
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('fresh but unscoreable newest row stays unknown', r->>'assessment_id' = newer::text
        AND r->>'status' = 'UNKNOWN' AND r->>'freshness_status' = 'fresh' AND r->'score' = 'null');
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '1 minute', 'unknown', true);
    PERFORM pg_temp.check_result('stored unknown stays unknown', public.get_latest_risk_assessment()->>'status' = 'UNKNOWN'
        AND public.get_latest_risk_assessment()->>'reason' = 'ASSESSMENT_UNKNOWN');

    -- Preserve a stored degradation even if its fresh_until lies in the future.
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment(1, interval '1 minute', 'degraded');
    PERFORM pg_temp.check_result('stored degraded is not upgraded', public.get_latest_risk_assessment()->>'status' = 'DEGRADED');
    -- Already stale at assessment time: use the persisted expired deadline directly.
    d := pg_temp.read_doc(aid);
    d := d || jsonb_build_object('freshness_status', 'stale');
    d := jsonb_set(d, '{freshness}', (d->'freshness') || jsonb_build_object('status', 'STALE',
        'fresh_until', deadline - interval '30 minutes', 'expires_at', deadline - interval '2 minutes'));
    UPDATE public.assessments SET freshness_status = 'stale', stale_after = deadline - interval '2 minutes' WHERE id = aid;
    PERFORM pg_temp.replace_doc(aid, d);
    PERFORM pg_temp.check_result('stored stale cannot serve score', public.get_latest_risk_assessment()->>'status' = 'STALE'
        AND public.get_latest_risk_assessment()->'score' = 'null');
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment();
    d := pg_temp.read_doc(aid);
    PERFORM pg_temp.replace_doc(aid, jsonb_set(d, '{score}', '90'));
    PERFORM pg_temp.check_result('summary document mismatch', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, jsonb_set(d, '{snapshot_id}', '"different-snapshot"'));
    PERFORM pg_temp.check_result('snapshot identity mismatch', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, jsonb_set(d, '{freshness,expires_at}', '"not-a-timestamp"'));
    PERFORM pg_temp.check_result('malformed deadline fails closed', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, jsonb_set(d, '{factors,0,explanation}', to_jsonb(repeat('x', 2001))));
    PERFORM pg_temp.check_result('oversized finding rejected', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, d);
    UPDATE public.evidence_records SET evidence_json = jsonb_set(evidence_json, '{raw_result}', '"{broken json"')
    WHERE assessment_id = aid AND evidence_type = 'service_policy';
    PERFORM pg_temp.check_result('malformed serialized assessment fails closed', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, d);
    UPDATE public.evidence_records SET evidence_json = jsonb_set(evidence_json, '{content_hash}', '"bad-hash"')
    WHERE assessment_id = aid AND evidence_type = 'service_policy';
    PERFORM pg_temp.check_result('serialized content hash checked', public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, d);
END;
$tests$;

-- Reproduce hosted rounded float output without changing any stored deadline.
DO $confidence_tests$
DECLARE
    aid uuid;
    doc jsonb;
    r jsonb;
    original_float_digits text := current_setting('extra_float_digits');
BEGIN
    PERFORM set_config('extra_float_digits', '0', true);
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    aid := pg_temp.seed_assessment();
    UPDATE public.assessments SET confidence = 0.5714285714285714::double precision WHERE id = aid;
    doc := jsonb_set(pg_temp.read_doc(aid), '{confidence}', '0.5714285714285714'::jsonb);
    PERFORM pg_temp.replace_doc(aid, doc);
    PERFORM pg_temp.check_result('hosted precision case reproduced', (
        SELECT (doc->'confidence')::text = '0.5714285714285714'
            AND to_jsonb(confidence)::text = '0.571428571428571'
            AND doc->'confidence' IS DISTINCT FROM to_jsonb(confidence)
            AND (doc->>'confidence')::double precision = confidence
        FROM public.assessments WHERE id = aid));
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('equivalent confidence doubles accepted despite JSON precision',
        r->>'status' = 'OK' AND r->>'assessment_id' = aid::text AND r->'score' = '20');

    PERFORM pg_temp.replace_doc(aid, jsonb_set(doc, '{confidence}', '0.6'));
    r := public.get_latest_risk_assessment();
    PERFORM pg_temp.check_result('genuinely different confidence rejected',
        r->>'reason' = 'INVALID_PUBLICATION' AND r->>'status' = 'UNKNOWN' AND r->'score' = 'null');
    PERFORM pg_temp.replace_doc(aid, jsonb_set(doc, '{confidence}', '0.5714285714285715'));
    PERFORM pg_temp.check_result('nearby distinct confidence double rejected without tolerance',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');

    PERFORM pg_temp.replace_doc(aid, jsonb_set(doc, '{confidence}', 'null'));
    PERFORM pg_temp.check_result('null document confidence rejects nonnull column',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    UPDATE public.assessments SET confidence = NULL WHERE id = aid;
    PERFORM pg_temp.check_result('null confidence matches null column',
        public.get_latest_risk_assessment()->>'status' = 'OK'
        AND public.get_latest_risk_assessment()->'confidence' = 'null');
    PERFORM pg_temp.replace_doc(aid, doc);
    PERFORM pg_temp.check_result('nonnull document confidence rejects null column',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, doc - 'confidence');
    PERFORM pg_temp.check_result('missing confidence is not JSON null',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    UPDATE public.assessments SET confidence = 0.5714285714285714::double precision WHERE id = aid;
    PERFORM pg_temp.replace_doc(aid, jsonb_set(doc, '{confidence}', '"0.5714285714285714"'));
    PERFORM pg_temp.check_result('numeric string confidence remains invalid',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM pg_temp.replace_doc(aid, jsonb_set(doc, '{confidence}', '{"invalid":true}'));
    PERFORM pg_temp.check_result('object confidence remains invalid',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');

    -- The score integrity comparison must retain its original exact JSON semantics.
    UPDATE public.assessments SET score = 20.123456789012344 WHERE id = aid;
    doc := jsonb_set(doc, '{score}', '20.123456789012344');
    PERFORM pg_temp.replace_doc(aid, doc);
    PERFORM pg_temp.check_result('score serialization integrity check remains unchanged',
        public.get_latest_risk_assessment()->>'reason' = 'INVALID_PUBLICATION');
    PERFORM set_config('extra_float_digits', original_float_digits, true);
    TRUNCATE public.evidence_records, public.reserve_snapshots, public.assessments;
    PERFORM pg_temp.seed_assessment(); -- Keep the subsequent ACL execution fixture fresh.
END;
$confidence_tests$;
DO $acl$
DECLARE
    fn regprocedure := 'public.get_latest_risk_assessment(text,text,text,text)'::regprocedure;
    role_name text;
    table_name text;
    denied boolean;
BEGIN
    PERFORM pg_temp.check_result('reader can execute', has_function_privilege('risk_api_reader', fn, 'EXECUTE'));
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        PERFORM pg_temp.check_result(role_name || ' cannot execute', NOT has_function_privilege(role_name, fn, 'EXECUTE'));
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['assessments', 'reserve_snapshots', 'evidence_records'] LOOP
        PERFORM pg_temp.check_result('reader has no raw access: ' || table_name,
            NOT has_table_privilege('risk_api_reader', 'public.' || table_name, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'));
        PERFORM pg_temp.check_result('owner read only: ' || table_name,
            has_table_privilege('risk_api_owner', 'public.' || table_name, 'SELECT')
            AND NOT has_table_privilege('risk_api_owner', 'public.' || table_name, 'INSERT,UPDATE,DELETE,TRUNCATE'));
    END LOOP;
    PERFORM pg_temp.check_result('reader not owner member', NOT pg_has_role('risk_api_reader', 'risk_api_owner', 'MEMBER'));
    PERFORM pg_temp.check_result('owner cannot login or bypass RLS', (SELECT NOT rolcanlogin AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreaterole FROM pg_roles WHERE rolname = 'risk_api_owner'));
    PERFORM pg_temp.check_result('function owner and fixed search path', (SELECT proowner = 'risk_api_owner'::regrole
        AND prosecdef AND provolatile = 's' AND 'search_path=pg_catalog, pg_temp' = ANY(proconfig)
        FROM pg_proc WHERE oid = fn));
    EXECUTE 'SET LOCAL ROLE risk_api_reader';
    IF public.get_latest_risk_assessment()->>'status' IS DISTINCT FROM 'OK' THEN
        RAISE EXCEPTION 'Reader could not use definer/RLS projection';
    END IF;
    denied := false;
    BEGIN PERFORM 1 FROM public.assessments; EXCEPTION WHEN insufficient_privilege THEN denied := true; END;
    IF NOT denied THEN RAISE EXCEPTION 'Raw table access unexpectedly succeeded'; END IF;
    EXECUTE 'RESET ROLE';
    PERFORM pg_temp.check_result('actual reader execution with RLS and raw access denial', true);
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        EXECUTE format('SET LOCAL ROLE %I', role_name);
        denied := false;
        BEGIN PERFORM public.get_latest_risk_assessment(); EXCEPTION WHEN insufficient_privilege THEN denied := true; END;
        EXECUTE 'RESET ROLE';
        PERFORM pg_temp.check_result(role_name || ' execution actually denied', denied);
    END LOOP;
END;
$acl$;
SELECT name AS passed FROM serving_test_results ORDER BY name;
SELECT count(*) AS passed_tests FROM serving_test_results;
ROLLBACK;
