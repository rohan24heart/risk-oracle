-- Run as administrator in an empty isolated database after all migrations:
-- psql -X -v ON_ERROR_STOP=1 -f supabase/tests/query_events.sql
-- Assertions fail immediately; all fixtures and temporary grants roll back.
BEGIN;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.query_events) THEN
        RAISE EXCEPTION 'Query event tests require an empty isolated database';
    END IF;
END $$;
CREATE TEMP TABLE query_event_test_results (name text PRIMARY KEY);
CREATE FUNCTION pg_temp.check_event(test_name text, passed boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF passed IS NOT TRUE THEN RAISE EXCEPTION 'FAIL: %', test_name; END IF;
    INSERT INTO query_event_test_results VALUES (test_name);
END $$;

CREATE FUNCTION pg_temp.emit_event(overrides jsonb DEFAULT '{}') RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    e jsonb := jsonb_build_object(
        'id', gen_random_uuid(), 'chain', 'base', 'protocol', 'aave-v3',
        'asset', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'assessment_id', '00000000-0000-4000-8000-000000000001',
        'methodology_version', 'v0.1', 'score', 20, 'risk_level', 'low',
        'freshness_status', 'fresh', 'payer_identifier', repeat('a', 64),
        'payment_reference', '0x' || repeat('b', 64),
        'payment_amount_atomic', '9007199254740993', 'payment_network', 'eip155:8453',
        'latency_ms', 125) || overrides;
BEGIN
    PERFORM public.record_query_event(
        (e->>'id')::uuid, e->>'chain', e->>'protocol', e->>'asset',
        (e->>'assessment_id')::uuid, e->>'methodology_version', (e->>'score')::double precision,
        e->>'risk_level', e->>'freshness_status', e->>'payer_identifier',
        e->>'payment_reference', e->>'payment_amount_atomic', e->>'payment_network',
        (e->>'latency_ms')::integer);
END $$;

DO $tests$
DECLARE
    fn regprocedure := 'public.record_query_event(uuid,text,text,text,uuid,text,double precision,text,text,text,text,text,text,integer)'::regprocedure;
    r text;
    t text;
    operation text;
    bad jsonb;
    field text;
    denied boolean;
    original_result jsonb := public.get_latest_risk_assessment();
BEGIN
    PERFORM pg_temp.check_event('only allowlisted columns', (
        SELECT array_agg(attname::text ORDER BY attnum) = ARRAY[
            'id','created_at','chain','protocol','asset','assessment_id','methodology_version',
            'score','risk_level','freshness_status','payer_identifier','payment_reference',
            'payment_amount_atomic','payment_network','latency_ms']
        FROM pg_attribute WHERE attrelid = 'public.query_events'::regclass AND attnum > 0 AND NOT attisdropped));
    PERFORM pg_temp.check_event('forced RLS', (SELECT relrowsecurity AND relforcerowsecurity
        FROM pg_class WHERE oid = 'public.query_events'::regclass));
    PERFORM pg_temp.check_event('restricted definer and search path', (SELECT prosecdef
        AND proowner = 'query_events_owner'::regrole AND provolatile = 'v'
        AND 'search_path=pg_catalog, pg_temp' = ANY(proconfig) FROM pg_proc WHERE oid = fn));
    PERFORM pg_temp.check_event('owner cannot login or bypass RLS', (SELECT NOT rolcanlogin
        AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb
        AND NOT rolinherit FROM pg_roles WHERE rolname = 'query_events_owner'));
    PERFORM pg_temp.check_event('reader execute allowed', has_function_privilege('risk_api_reader', fn, 'EXECUTE'));
    PERFORM pg_temp.check_event('owner insert only', has_table_privilege('query_events_owner', 'public.query_events', 'INSERT')
        AND NOT has_table_privilege('query_events_owner', 'public.query_events', 'SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'));
    FOREACH r IN ARRAY ARRAY['risk_api_reader','risk_api_owner','anon','authenticated','service_role'] LOOP
        PERFORM pg_temp.check_event(r || ' no raw telemetry access', NOT has_table_privilege(r,
            'public.query_events', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'));
        PERFORM pg_temp.check_event(r || ' not telemetry owner member', NOT pg_has_role(r, 'query_events_owner', 'MEMBER'));
        IF r <> 'risk_api_reader' THEN
            PERFORM pg_temp.check_event(r || ' no execute', NOT has_function_privilege(r, fn, 'EXECUTE'));
            EXECUTE format('SET LOCAL ROLE %I', r);
            denied := false;
            BEGIN PERFORM pg_temp.emit_event(); EXCEPTION WHEN insufficient_privilege THEN denied := true; END;
            RESET ROLE;
            PERFORM pg_temp.check_event(r || ' actual execute denied', denied);
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['assessments','reserve_snapshots','evidence_records'] LOOP
        FOREACH r IN ARRAY ARRAY['query_events_owner','risk_api_reader'] LOOP
            PERFORM pg_temp.check_event(r || ' no risk table access: ' || t,
                NOT has_table_privilege(r, 'public.' || t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'));
        END LOOP;
    END LOOP;
    PERFORM pg_temp.check_event('no serving owner membership', NOT pg_has_role('query_events_owner', 'risk_api_owner', 'MEMBER'));
    PERFORM pg_temp.check_event('no serving RPC access for telemetry owner', NOT has_function_privilege(
        'query_events_owner', 'public.get_latest_risk_assessment(text,text,text,text)', 'EXECUTE'));

    SET LOCAL ROLE risk_api_reader;
    PERFORM pg_temp.emit_event('{"id":"00000000-0000-4000-8000-000000000002"}');
    PERFORM pg_temp.emit_event('{"freshness_status":"degraded","latency_ms":0}');
    denied := false;
    BEGIN
        PERFORM pg_temp.emit_event('{"id":"00000000-0000-4000-8000-000000000002"}');
    EXCEPTION WHEN unique_violation THEN denied := true; END;
    RESET ROLE;
    PERFORM pg_temp.check_event('duplicate event rejected', denied);
    PERFORM pg_temp.check_event('one row per call and exact atomic units', (SELECT count(*) = 2
        AND bool_and(payment_amount_atomic = '9007199254740993' AND score = 20 AND risk_level = 'low'
            AND payer_identifier = repeat('a',64) AND created_at = statement_timestamp()) FROM public.query_events));

    FOREACH operation IN ARRAY ARRAY[
        'SELECT * FROM public.query_events',
        'INSERT INTO public.query_events DEFAULT VALUES',
        'UPDATE public.query_events SET latency_ms = 1',
        'DELETE FROM public.query_events', 'TRUNCATE public.query_events'] LOOP
        SET LOCAL ROLE risk_api_reader;
        denied := false;
        BEGIN EXECUTE operation; EXCEPTION WHEN insufficient_privilege THEN denied := true; END;
        RESET ROLE;
        PERFORM pg_temp.check_event('reader denied: ' || operation, denied);
    END LOOP;

    -- All fields are mandatory; null cannot bypass CHECK constraints.
    FOREACH field IN ARRAY ARRAY['id','chain','protocol','asset','assessment_id','methodology_version',
        'score','risk_level','freshness_status','payer_identifier','payment_reference',
        'payment_amount_atomic','payment_network','latency_ms'] LOOP
        SET LOCAL ROLE risk_api_reader;
        denied := false;
        BEGIN PERFORM pg_temp.emit_event(jsonb_build_object(field, NULL));
        EXCEPTION WHEN not_null_violation THEN denied := true; END;
        RESET ROLE;
        PERFORM pg_temp.check_event('null rejected: ' || field, denied);
    END LOOP;
    FOR bad IN SELECT value FROM jsonb_array_elements('[
        {"chain":"ethereum"}, {"protocol":"other"}, {"asset":"USDC"},
        {"methodology_version":"v0.2"}, {"score":-1}, {"score":101}, {"score":"NaN"},
        {"score":"Infinity"}, {"risk_level":"unknown"}, {"freshness_status":"stale"},
        {"freshness_status":"unknown"}, {"payer_identifier":"0x1234"},
        {"payment_reference":""}, {"payment_reference":"Bearer secret"},
        {"payment_amount_atomic":"0"}, {"payment_amount_atomic":"-1"},
        {"payment_amount_atomic":"01"}, {"payment_amount_atomic":"1.5"},
        {"payment_amount_atomic":"1e6"},
        {"payment_amount_atomic":"115792089237316195423570985008687907853269984665640564039457584007913129639936"},
        {"payment_network":"base"}, {"latency_ms":-1}
    ]'::jsonb) UNION ALL SELECT jsonb_build_object('payment_reference', repeat('a',257)) LOOP
        SET LOCAL ROLE risk_api_reader;
        denied := false;
        BEGIN PERFORM pg_temp.emit_event(bad);
        EXCEPTION WHEN check_violation OR invalid_text_representation THEN denied := true; END;
        RESET ROLE;
        PERFORM pg_temp.check_event('invalid input rejected: ' || bad::text, denied);
    END LOOP;
    PERFORM pg_temp.check_event('failed calls append nothing', (SELECT count(*) = 2 FROM public.query_events));
    SET LOCAL ROLE risk_api_reader;
    denied := public.get_latest_risk_assessment() = original_result;
    RESET ROLE;
    PERFORM pg_temp.check_event('reader serving result unchanged', denied);
END;
$tests$;

-- Even accidental table grants cannot bypass the absence of an RLS policy.
GRANT SELECT, INSERT ON public.query_events TO anon;
DO $rls$
DECLARE denied boolean := false; rows_seen integer;
BEGIN
    SET LOCAL ROLE anon;
    SELECT count(*) INTO rows_seen FROM public.query_events;
    BEGIN INSERT INTO public.query_events DEFAULT VALUES;
    EXCEPTION WHEN insufficient_privilege THEN denied := true; END;
    RESET ROLE;
    PERFORM pg_temp.check_event('RLS hides events despite SELECT grant', rows_seen = 0);
    PERFORM pg_temp.check_event('RLS rejects insert despite INSERT grant', denied);
END;
$rls$;
SELECT name AS passed FROM query_event_test_results ORDER BY name;
SELECT count(*) AS passed_tests FROM query_event_test_results;
ROLLBACK;
