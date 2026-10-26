BEGIN;
-- v0.13: apply only to an existing 0009 schema as its trusted migration owner.
-- No auth schema access, user-data reset, ledger rewrite or permission expansion.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE lolpick.lolpick_seasons IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE lolpick.lolpick_matches IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE lolpick.lolpick_members IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE lolpick.lolpick_point_transactions IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
 IF (SELECT version_num FROM lolpick.lolpick_alembic_version) IS DISTINCT FROM '0009_user_actions' THEN
  RAISE EXCEPTION 'Expected revision 0009_user_actions';
 END IF;
 IF EXISTS (
  SELECT 1 FROM lolpick.lolpick_point_transactions t GROUP BY member_id,season_id
  HAVING count(*) FILTER (WHERE reason=1) <> 1 OR sum(amount::numeric) NOT BETWEEN 0 AND 9007199254740991
     OR sum(amount::numeric) IS DISTINCT FROM (array_agg(balance_after ORDER BY id DESC))[1]::numeric
 ) OR EXISTS (
  SELECT 1 FROM lolpick.lolpick_members m
  WHERE (m.current_season_id IS NULL AND m.current_points <> 0)
   OR (m.current_season_id IS NOT NULL AND m.current_points::numeric IS DISTINCT FROM
      (SELECT sum(t.amount::numeric) FROM lolpick.lolpick_point_transactions t
       WHERE t.member_id=m.id AND t.season_id=m.current_season_id))
 ) THEN
  RAISE EXCEPTION 'Ledger integrity check failed; migration aborted without repairing data';
 END IF;
END $$;
DROP INDEX IF EXISTS lolpick.uq_seasons_one_active;
CREATE INDEX IF NOT EXISTS ix_seasons_status ON lolpick.lolpick_seasons(status);
CREATE INDEX IF NOT EXISTS ix_points_member_season_id ON lolpick.lolpick_point_transactions(member_id,season_id,id);
ALTER TABLE lolpick.lolpick_system_logs DROP CONSTRAINT lolpick_system_logs_route_code_check;
ALTER TABLE lolpick.lolpick_system_logs ADD CONSTRAINT lolpick_system_logs_route_code_check CHECK (route_code BETWEEN 0 AND 19);
ALTER TABLE lolpick.lolpick_system_logs DROP CONSTRAINT lolpick_system_logs_context_check;
ALTER TABLE lolpick.lolpick_system_logs ADD CONSTRAINT lolpick_system_logs_context_check CHECK (context IS NULL OR
  (jsonb_typeof(context) = 'object' AND octet_length(context::text) <= 1024
   AND context - ARRAY['match_id','entry_id','count','error_type','inserted','updated','skipped',
    'conflicts','series_id','pool','paid','removed_remainder','error_code','settlement_blocked',
    'response_complete','reason','legacy_event_name','legacy_feature','legacy_action','archive_file']::text[] = '{}'::jsonb));
CREATE OR REPLACE FUNCTION lolpick.post_point_entry() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_match lolpick.lolpick_matches%ROWTYPE; v_member lolpick.lolpick_members%ROWTYPE;
 v_stake lolpick.lolpick_point_transactions%ROWTYPE; v_contract lolpick.lolpick_cheer_contracts%ROWTYPE;
 v_total numeric; v_openings bigint; v_latest bigint; v_count bigint;
 v_status smallint; v_initial bigint; v_demo boolean; v_next numeric; v_chosen bigint; v_pool numeric; v_posted numeric;
BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Point ledger is append-only'; END IF;
 SELECT status,initial_points,is_demo INTO v_status,v_initial,v_demo
  FROM lolpick.lolpick_seasons WHERE id = NEW.season_id FOR SHARE;
 IF NOT FOUND OR v_demo THEN RAISE EXCEPTION 'Unknown or demo season'; END IF;
 IF NEW.match_id IS NOT NULL THEN
  SELECT * INTO v_match FROM lolpick.lolpick_matches WHERE id = NEW.match_id FOR UPDATE;
  IF NOT FOUND OR v_match.season_id IS DISTINCT FROM NEW.season_id THEN RAISE EXCEPTION 'Unknown match or incorrect season'; END IF;
  IF v_match.is_demo THEN RAISE EXCEPTION 'Demo matches cannot post real points'; END IF;
 END IF;
 SELECT * INTO v_member FROM lolpick.lolpick_members WHERE id = NEW.member_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Unknown member'; END IF;
 SELECT COALESCE(sum(amount::numeric),0),count(*),count(*) FILTER (WHERE reason = 1)
  INTO v_total,v_count,v_openings FROM lolpick.lolpick_point_transactions
  WHERE member_id = NEW.member_id AND season_id = NEW.season_id;
 SELECT balance_after INTO v_latest FROM lolpick.lolpick_point_transactions
  WHERE member_id = NEW.member_id AND season_id = NEW.season_id ORDER BY id DESC LIMIT 1;
 IF NEW.reason = 1 THEN
  IF v_status <> 2 OR NEW.amount IS DISTINCT FROM v_initial OR v_count <> 0 THEN
   RAISE EXCEPTION 'Invalid or duplicate season opening';
  END IF;
  v_next := NEW.amount;
 ELSE
  IF v_status <> 2 OR v_openings <> 1 OR v_latest IS DISTINCT FROM v_total
   OR (v_member.current_season_id = NEW.season_id AND v_member.current_points::numeric <> v_total) THEN
   RAISE EXCEPTION 'Invalid season ledger or inactive season';
  END IF;
  IF NEW.reason = 5 THEN
   IF v_status <> 2 OR v_match.status IS DISTINCT FROM 1 OR v_match.result IS NOT NULL
    OR v_match.settlement_status IS DISTINCT FROM 1 OR v_match.provider_status IS DISTINCT FROM 1
    OR v_match.issue_flags <> 0 OR v_match.team_a_id IS NULL OR v_match.team_b_id IS NULL
    OR v_match.starts_at IS NULL OR v_match.starts_at <= clock_timestamp() THEN
    RAISE EXCEPTION 'Match is not eligible for a wager';
   END IF;
   IF NEW.selected_side IS NULL OR NEW.selected_side NOT IN (1,2) THEN RAISE EXCEPTION 'Unknown wager side'; END IF;
   IF NEW.contract_id IS NOT NULL THEN
    SELECT * INTO v_contract FROM lolpick.lolpick_cheer_contracts WHERE id = NEW.contract_id FOR UPDATE;
    IF NOT FOUND OR v_contract.member_id IS DISTINCT FROM NEW.member_id OR v_contract.season_id IS DISTINCT FROM NEW.season_id
     OR v_contract.finalized_at IS NOT NULL THEN RAISE EXCEPTION 'Invalid cheer contract'; END IF;
    v_chosen := CASE WHEN NEW.selected_side = 1 THEN v_match.team_a_id ELSE v_match.team_b_id END;
    IF NEW.cheer_action IS DISTINCT FROM (CASE WHEN v_chosen = v_contract.team_id THEN 1 ELSE 2 END)::smallint THEN
     RAISE EXCEPTION 'Cheer action differs from selected team';
    END IF;
   END IF;
  ELSIF NEW.reason IN (6,7) THEN
   IF v_match.settlement_status IS DISTINCT FROM 1 OR v_match.result IS NULL
    OR (v_match.issue_flags & 4064) <> 0 THEN RAISE EXCEPTION 'Match is not eligible for settlement'; END IF;
   SELECT * INTO v_stake FROM lolpick.lolpick_point_transactions WHERE id = NEW.related_entry_id;
   IF NOT FOUND OR v_stake.reason <> 5 OR v_stake.member_id IS DISTINCT FROM NEW.member_id
    OR v_stake.season_id IS DISTINCT FROM NEW.season_id OR v_stake.match_id IS DISTINCT FROM NEW.match_id THEN
    RAISE EXCEPTION 'Final post does not reference this member/match wager';
   END IF;
   IF NEW.reason = 6 THEN
    IF v_match.status <> 3 OR v_match.result NOT IN (1,2) OR v_stake.selected_side <> v_match.result
     OR v_match.issue_flags <> 0 THEN RAISE EXCEPTION 'Payout requires an unambiguous winning wager'; END IF;
    SELECT COALESCE(sum(-amount::numeric),0) INTO v_pool FROM lolpick.lolpick_point_transactions
     WHERE match_id = NEW.match_id AND reason = 5;
    SELECT COALESCE(sum(amount::numeric),0) INTO v_posted FROM lolpick.lolpick_point_transactions
     WHERE match_id = NEW.match_id AND reason = 6;
    IF v_posted + NEW.amount::numeric > v_pool THEN RAISE EXCEPTION 'Payouts exceed funded pool'; END IF;
   ELSE
    IF NEW.amount IS DISTINCT FROM -v_stake.amount THEN RAISE EXCEPTION 'Refund must equal the original stake'; END IF;
    IF v_match.result IN (1,2) AND EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions
      WHERE match_id = NEW.match_id AND reason = 5 AND selected_side = v_match.result) THEN
     RAISE EXCEPTION 'Winning wagers exist: refund is not permitted';
    END IF;
   END IF;
  ELSE
   IF v_status <> 2 THEN RAISE EXCEPTION 'Non-settlement posts require an active season'; END IF;
  END IF;
  v_next := v_total + NEW.amount::numeric;
 END IF;
 IF v_next IS NULL OR v_next < 0 OR v_next > 9007199254740991 THEN RAISE EXCEPTION 'Insufficient points or balance overflow'; END IF;
 NEW.balance_after := v_next::bigint; NEW.created_at := clock_timestamp();
 UPDATE lolpick.lolpick_members SET current_season_id = NEW.season_id,current_points = NEW.balance_after
  WHERE id = NEW.member_id;
 RETURN NEW;
END $$;
COMMENT ON COLUMN lolpick.lolpick_members.current_points IS 'Last-touched season cache only; use the member+season ledger for balances.';
COMMENT ON COLUMN lolpick.lolpick_members.current_season_id IS 'Cache scope, not an exclusive season membership or UI selection.';
UPDATE lolpick.lolpick_alembic_version SET version_num='0010_concurrent_seasons';
COMMIT;
