-- Precomputed v0 storage only. Apply through an authorized migration role later.
-- Canonical decimal text keeps uint256 values exact across Postgres/PostgREST/JS.
BEGIN;

CREATE TABLE public.assessments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chain text NOT NULL CHECK (chain = 'base'),
    protocol text NOT NULL CHECK (protocol = 'aave-v3'),
    asset text NOT NULL CHECK (asset ~ '^0x[0-9a-f]{40}$'),
    score smallint CHECK (score BETWEEN 0 AND 2),
    risk_level text NOT NULL CHECK (risk_level IN ('NO_TRIGGER', 'WATCH', 'ALERT', 'UNKNOWN')),
    confidence text CHECK (confidence IN ('COMPLETE', 'DEGRADED', 'INSUFFICIENT')),
    freshness_status text NOT NULL CHECK (freshness_status IN ('FRESH', 'DEGRADED', 'STALE', 'UNKNOWN')),
    calculated_at timestamptz NOT NULL CHECK (isfinite(calculated_at)),
    stale_after timestamptz CHECK (isfinite(stale_after)),
    block_number text NOT NULL,
    methodology_version text NOT NULL CHECK (methodology_version ~ '\S' AND methodology_version = btrim(methodology_version)),
    created_at timestamptz NOT NULL DEFAULT now() CHECK (isfinite(created_at)),
    CONSTRAINT assessments_block_uint256 CHECK (
        block_number ~ '^(0|[1-9][0-9]{0,77})$'
        AND block_number::numeric <= 115792089237316195423570985008687907853269984665640564039457584007913129639935
    ),
    -- Store the existing shared model's ordinal result; this defines no scoring formula.
    CONSTRAINT assessments_result_consistency CHECK (
        (freshness_status = 'UNKNOWN' AND score IS NULL AND risk_level = 'UNKNOWN'
            AND (confidence IS NULL OR confidence = 'INSUFFICIENT'))
        OR
        (freshness_status <> 'UNKNOWN' AND score IS NOT NULL
            AND risk_level = CASE score WHEN 0 THEN 'NO_TRIGGER' WHEN 1 THEN 'WATCH' WHEN 2 THEN 'ALERT' END
            AND (confidence IS NULL OR confidence IN ('COMPLETE', 'DEGRADED')))
    ),
    CONSTRAINT assessments_deadlines CHECK (
        (freshness_status = 'UNKNOWN' AND stale_after IS NULL)
        OR (freshness_status IN ('FRESH', 'DEGRADED') AND stale_after IS NOT NULL AND stale_after > calculated_at)
        OR (freshness_status = 'STALE' AND stale_after IS NOT NULL AND stale_after <= calculated_at)
    ),
    CONSTRAINT assessments_identity_unique UNIQUE (chain, protocol, asset, block_number, methodology_version),
    -- Referenced by snapshots to reject a different asset/block without a trigger.
    CONSTRAINT assessments_snapshot_identity_unique UNIQUE (id, asset, block_number)
);

CREATE INDEX assessments_latest_idx
    ON public.assessments (chain, protocol, asset, calculated_at DESC, created_at DESC, id DESC);

CREATE TABLE public.reserve_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id uuid NOT NULL,
    asset text NOT NULL CHECK (asset ~ '^0x[0-9a-f]{40}$'),
    block_number text NOT NULL,
    observed_at timestamptz NOT NULL CHECK (isfinite(observed_at)),
    snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now() CHECK (isfinite(created_at)),
    CONSTRAINT reserve_snapshots_assessment_fk FOREIGN KEY (assessment_id, asset, block_number)
        REFERENCES public.assessments (id, asset, block_number) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT reserve_snapshots_assessment_unique UNIQUE (assessment_id),
    -- Preserve the shared model's string wire encoding and subject/block identity.
    CONSTRAINT reserve_snapshots_json_identity CHECK (
        jsonb_typeof(snapshot_json #> '{block,number}') = 'string'
        AND (snapshot_json #>> '{block,number}') IS NOT NULL
        AND snapshot_json #>> '{block,number}' = block_number
        AND (snapshot_json #>> '{subject,asset}') IS NOT NULL
        AND snapshot_json #>> '{subject,asset}' = asset
    )
);

CREATE TABLE public.evidence_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id uuid NOT NULL REFERENCES public.assessments (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    evidence_type text NOT NULL CHECK (evidence_type IN ('onchain_rpc', 'official_document', 'service_policy')),
    source text NOT NULL CHECK (source ~ '\S'),
    source_url text CHECK (source_url ~ '\S'),
    observed_at timestamptz NOT NULL CHECK (isfinite(observed_at)),
    evidence_json jsonb NOT NULL CHECK (jsonb_typeof(evidence_json) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now() CHECK (isfinite(created_at))
);

CREATE INDEX evidence_records_assessment_idx ON public.evidence_records (assessment_id);

-- No anon/authenticated/PUBLIC privileges, no permissive policies, no public views.
ALTER TABLE public.assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assessments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.reserve_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reserve_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE public.evidence_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evidence_records FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.assessments, public.reserve_snapshots, public.evidence_records
    FROM PUBLIC, anon, authenticated, service_role;
-- Trusted ingestion can append and inspect history. No update/delete/truncate grant.
-- Supabase's service_role bypasses RLS; its credentials must stay server-side.
GRANT SELECT, INSERT ON TABLE public.assessments, public.reserve_snapshots, public.evidence_records
    TO service_role;

COMMIT;
