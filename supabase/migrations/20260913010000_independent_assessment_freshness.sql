-- Freshness describes data age independently from the availability of a score.
BEGIN;
ALTER TABLE public.assessments DROP CONSTRAINT assessments_result_consistency;
ALTER TABLE public.assessments ADD CONSTRAINT assessments_result_consistency CHECK (
    (score IS NULL AND risk_level = 'unknown' AND confidence IS NULL)
    OR (score IS NOT NULL AND freshness_status <> 'unknown' AND risk_level =
        CASE WHEN score < 25 THEN 'low' WHEN score < 50 THEN 'moderate'
             WHEN score < 75 THEN 'high' ELSE 'critical' END)
);
-- Age policy uses exclusive deadlines one microsecond past inclusive boundaries.
-- No existing data, permissions, scoring rules or other constraints are changed.
COMMIT;
