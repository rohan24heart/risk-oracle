-- Persistence only: the public endpoint must not call this until payment settles.

BEGIN;

-- Fail on a role collision. This owner receives no risk-data privileges.
CREATE ROLE query_events_owner
    NOLOGIN
    NOINHERIT
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOBYPASSRLS;

GRANT USAGE ON SCHEMA public TO query_events_owner;

CREATE TABLE public.query_events (
    id uuid PRIMARY KEY,

    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),

    chain text NOT NULL
        CHECK (chain = 'base'),

    protocol text NOT NULL
        CHECK (protocol = 'aave-v3'),

    asset text NOT NULL
        CHECK (
            asset = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
        ),

    -- Deliberately no FK/read of assessments: telemetry cannot expose historical
    -- assessment existence or interfere with publication retention.
    assessment_id uuid NOT NULL,

    methodology_version text NOT NULL
        CHECK (methodology_version = 'v0.1'),

    score double precision NOT NULL
        CHECK (score BETWEEN 0 AND 100),

    risk_level text NOT NULL
        CHECK (
            risk_level IN ('low', 'moderate', 'high', 'critical')
        ),

    freshness_status text NOT NULL
        CHECK (
            freshness_status IN ('fresh', 'degraded')
        ),

    -- Caller supplies a keyed pseudonym (HMAC-SHA256 hex), never a raw wallet.
    payer_identifier text NOT NULL
        CHECK (
            payer_identifier ~ '^[0-9a-f]{64}$'
        ),

    payment_reference text NOT NULL
        CHECK (
            length(payment_reference) BETWEEN 1 AND 256
            AND payment_reference !~ '[^A-Za-z0-9_:.-]'
        ),

    -- Decimal text preserves atomic units across PostgREST/JavaScript without rounding.
    payment_amount_atomic text NOT NULL
        CHECK (
            payment_amount_atomic ~ '^[1-9][0-9]{0,77}$'
            AND payment_amount_atomic::numeric <=
                115792089237316195423570985008687907853269984665640564039457584007913129639935
        ),

    payment_network text NOT NULL
        CHECK (
            length(payment_network) <= 64
            AND payment_network ~ '^[a-z0-9-]{3,8}:[A-Za-z0-9_-]{1,32}$'
        ),

    latency_ms integer NOT NULL
        CHECK (latency_ms >= 0)
);

ALTER TABLE public.query_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.query_events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON public.query_events
    FROM PUBLIC,
         anon,
         authenticated,
         service_role,
         risk_api_reader,
         risk_api_owner,
         query_events_owner;

GRANT INSERT ON public.query_events TO query_events_owner;

CREATE POLICY query_events_owner_insert
    ON public.query_events
    FOR INSERT
    TO query_events_owner
    WITH CHECK (true);

CREATE FUNCTION public.record_query_event(
    p_event_id uuid,
    p_chain text,
    p_protocol text,
    p_asset text,
    p_assessment_id uuid,
    p_methodology_version text,
    p_score double precision,
    p_risk_level text,
    p_freshness_status text,
    p_payer_identifier text,
    p_payment_reference text,
    p_payment_amount_atomic text,
    p_payment_network text,
    p_latency_ms integer
)
RETURNS void
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
    INSERT INTO public.query_events (
        id,
        chain,
        protocol,
        asset,
        assessment_id,
        methodology_version,
        score,
        risk_level,
        freshness_status,
        payer_identifier,
        payment_reference,
        payment_amount_atomic,
        payment_network,
        latency_ms
    )
    VALUES (
        p_event_id,
        p_chain,
        p_protocol,
        p_asset,
        p_assessment_id,
        p_methodology_version,
        p_score,
        p_risk_level,
        p_freshness_status,
        p_payer_identifier,
        p_payment_reference,
        p_payment_amount_atomic,
        p_payment_network,
        p_latency_ms
    );
$function$;

REVOKE ALL ON FUNCTION public.record_query_event(
    uuid,
    text,
    text,
    text,
    uuid,
    text,
    double precision,
    text,
    text,
    text,
    text,
    text,
    text,
    integer
)
FROM PUBLIC,
     anon,
     authenticated,
     service_role,
     risk_api_owner;

GRANT EXECUTE ON FUNCTION public.record_query_event(
    uuid,
    text,
    text,
    text,
    uuid,
    text,
    double precision,
    text,
    text,
    text,
    text,
    text,
    text,
    integer
)
TO risk_api_reader;

COMMENT ON FUNCTION public.record_query_event(
    uuid,
    text,
    text,
    text,
    uuid,
    text,
    double precision,
    text,
    text,
    text,
    text,
    text,
    text,
    integer
) IS
    'One allowlisted successful paid-query event. Trusted caller must verify settlement, pseudonymize payer and isolate logging failures. Not called by the public endpoint yet. Reuse event UUID on retry; duplicates fail without appending.';

DO $ownership$
BEGIN
    EXECUTE format(
        'GRANT query_events_owner TO %I',
        current_user
    );
END;
$ownership$;

GRANT CREATE ON SCHEMA public TO query_events_owner;

ALTER FUNCTION public.record_query_event(
    uuid,
    text,
    text,
    text,
    uuid,
    text,
    double precision,
    text,
    text,
    text,
    text,
    text,
    text,
    integer
)
OWNER TO query_events_owner;

REVOKE CREATE ON SCHEMA public FROM query_events_owner;

DO $ownership$
BEGIN
    EXECUTE format(
        'REVOKE query_events_owner FROM %I',
        current_user
    );
END;
$ownership$;

COMMIT;