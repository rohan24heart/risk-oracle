-- Canonical 0-100 assessment contract. No scoring/ordinal conversion is performed.
BEGIN;
LOCK TABLE public.assessments IN ACCESS EXCLUSIVE MODE;

-- Legacy ordinal results cannot be truthfully relabeled as 0-100 results.
-- Abort rather than overwrite history or invent confidence mappings.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.assessments) THEN
        RAISE EXCEPTION 'Canonical assessment migration requires an empty assessments table. Preserve and explicitly reconcile existing legacy history before retrying.';
    END IF;
END;
$$;

ALTER TABLE public.assessments
    DROP CONSTRAINT assessments_score_check,
    DROP CONSTRAINT assessments_risk_level_check,
    DROP CONSTRAINT assessments_confidence_check,
    DROP CONSTRAINT assessments_freshness_status_check,
    DROP CONSTRAINT assessments_result_consistency,
    DROP CONSTRAINT assessments_deadlines;

ALTER TABLE public.assessments
    ALTER COLUMN score TYPE double precision USING score::double precision,
    ALTER COLUMN confidence TYPE double precision USING confidence::double precision;

ALTER TABLE public.assessments
    ADD CONSTRAINT assessments_score_check CHECK (score BETWEEN 0.0 AND 100.0),
    ADD CONSTRAINT assessments_confidence_check CHECK (confidence BETWEEN 0.0 AND 1.0),
    ADD CONSTRAINT assessments_risk_level_check CHECK (risk_level IN ('low', 'moderate', 'high', 'critical', 'unknown')),
    ADD CONSTRAINT assessments_freshness_status_check CHECK (freshness_status IN ('fresh', 'degraded', 'stale', 'unknown')),
    ADD CONSTRAINT assessments_result_consistency CHECK (
        (freshness_status = 'unknown' AND score IS NULL AND risk_level = 'unknown' AND confidence IS NULL)
        OR (freshness_status <> 'unknown' AND score IS NOT NULL AND risk_level =
            CASE WHEN score < 25 THEN 'low' WHEN score < 50 THEN 'moderate'
                 WHEN score < 75 THEN 'high' ELSE 'critical' END)
    ),
    ADD CONSTRAINT assessments_deadlines CHECK (
        (freshness_status = 'unknown' AND stale_after IS NULL)
        OR (freshness_status IN ('fresh', 'degraded') AND stale_after IS NOT NULL AND stale_after > calculated_at)
        OR (freshness_status = 'stale' AND stale_after IS NOT NULL AND stale_after <= calculated_at)
    );

-- UUIDs, uint256 decimal strings, FKs, unique keys, indexes, grants and RLS unchanged.
COMMIT;
