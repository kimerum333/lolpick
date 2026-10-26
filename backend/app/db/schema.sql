-- LoLPICK v7 unified-ledger design, 2026-10-09 (Asia/Seoul)
-- v7 bootstrap: existing schema makes CREATE SCHEMA fail.
-- Does not migrate existing data, modify auth/storage, or stamp Alembic.
-- Current nine-table schema. Native-session function and revision marker are added by ddl.render.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
CREATE SCHEMA lolpick;

CREATE TABLE lolpick.lolpick_api_sources (
 id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 code VARCHAR(80) NOT NULL UNIQUE CHECK (length(trim(code)) > 0)
);
CREATE TABLE lolpick.lolpick_seasons (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 public_code VARCHAR(80) NOT NULL UNIQUE CHECK (length(trim(public_code)) > 0),
 slug VARCHAR(40) NOT NULL CHECK (length(trim(slug)) > 0),
 year SMALLINT NOT NULL CHECK (year BETWEEN 2000 AND 9999),
 name VARCHAR(100) NOT NULL CHECK (length(trim(name)) > 0),
 label VARCHAR(120),
 status SMALLINT NOT NULL CHECK (status IN (1,2,3)),
 starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
 initial_points BIGINT NOT NULL DEFAULT 0 CHECK (initial_points BETWEEN 0 AND 9007199254740991),
 api_source_id SMALLINT REFERENCES lolpick.lolpick_api_sources(id),
 external_series_id VARCHAR(160), external_league_id VARCHAR(160),
 api_synced_at TIMESTAMPTZ,
 display_data JSONB CHECK (display_data IS NULL OR
  (jsonb_typeof(display_data) = 'object' AND octet_length(display_data::text) <= 2048
   AND display_data - ARRAY['league','series_name','last_counts','legacy_settings']::text[] = '{}'::jsonb)),
 is_demo BOOLEAN NOT NULL DEFAULT false,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(slug,year), UNIQUE(api_source_id,external_series_id),
 CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at),
 CHECK ((api_source_id IS NULL AND external_series_id IS NULL AND external_league_id IS NULL AND api_synced_at IS NULL)
  OR (api_source_id IS NOT NULL AND external_series_id IS NOT NULL AND length(trim(external_series_id)) > 0))
);
CREATE INDEX ix_seasons_status ON lolpick.lolpick_seasons(status);

CREATE TABLE lolpick.lolpick_members (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 auth_user_id UUID NOT NULL UNIQUE,
 google_sub VARCHAR(255) NOT NULL UNIQUE CHECK (length(trim(google_sub)) > 0),
 type SMALLINT NOT NULL DEFAULT 1 CHECK (type IN (1,2)),
 current_points BIGINT NOT NULL DEFAULT 0 CHECK (current_points BETWEEN 0 AND 9007199254740991),
 current_season_id BIGINT REFERENCES lolpick.lolpick_seasons(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (current_season_id IS NOT NULL OR current_points = 0)
);
CREATE INDEX ix_members_season ON lolpick.lolpick_members(current_season_id);

CREATE TABLE lolpick.lolpick_member_sessions (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 auth_session_id UUID NOT NULL UNIQUE,
 member_id BIGINT NOT NULL REFERENCES lolpick.lolpick_members(id),
 create_dt TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (1,2,3)),
 status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 revoked_at TIMESTAMPTZ,
 CHECK (expires_at = create_dt + INTERVAL '6 hours'),
 CHECK ((status = 3 AND revoked_at IS NOT NULL) OR (status <> 3 AND revoked_at IS NULL))
);
CREATE INDEX ix_sessions_member_status ON lolpick.lolpick_member_sessions(member_id,status);
CREATE INDEX ix_sessions_live_expiry ON lolpick.lolpick_member_sessions(expires_at) WHERE status = 1;

CREATE TABLE lolpick.lolpick_teams (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 api_source_id SMALLINT NOT NULL REFERENCES lolpick.lolpick_api_sources(id),
 external_team_id VARCHAR(160) NOT NULL CHECK (length(trim(external_team_id)) > 0),
 name VARCHAR(255) NOT NULL CHECK (length(trim(name)) > 0),
 acronym VARCHAR(80), logo_url VARCHAR(2048),
 display_data JSONB CHECK (display_data IS NULL OR
  (jsonb_typeof(display_data) = 'object' AND octet_length(display_data::text) <= 1024
   AND display_data - ARRAY['region','color','location']::text[] = '{}'::jsonb)),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(api_source_id,external_team_id), UNIQUE(id,api_source_id),
 CHECK (logo_url IS NULL OR logo_url LIKE 'https://%')
);

CREATE TABLE lolpick.lolpick_matches (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 public_code VARCHAR(80) NOT NULL UNIQUE CHECK (length(trim(public_code)) > 0),
 season_id BIGINT NOT NULL REFERENCES lolpick.lolpick_seasons(id),
 api_source_id SMALLINT NOT NULL REFERENCES lolpick.lolpick_api_sources(id),
 external_match_id VARCHAR(160) NOT NULL CHECK (length(trim(external_match_id)) > 0),
 round VARCHAR(120) NOT NULL,
 team_a_id BIGINT, team_b_id BIGINT,
 starts_at TIMESTAMPTZ,
 status SMALLINT NOT NULL CHECK (status IN (1,2,3,4)),
 score_a SMALLINT, score_b SMALLINT,
 result SMALLINT CHECK (result IN (1,2,3,4)),
 result_recorded_at TIMESTAMPTZ,
 provider_status SMALLINT CHECK (provider_status IN (1,2,3,4,5)),
 issue_flags SMALLINT NOT NULL DEFAULT 0 CHECK (issue_flags BETWEEN 0 AND 4095),
 is_forfeit BOOLEAN NOT NULL DEFAULT false,
 display_data JSONB CHECK (display_data IS NULL OR
  (jsonb_typeof(display_data) = 'object' AND octet_length(display_data::text) <= 2048
   AND display_data - ARRAY['name','league','tournament','match_type','number_of_games']::text[] = '{}'::jsonb)),
 api_synced_at TIMESTAMPTZ,
 settlement_status SMALLINT NOT NULL DEFAULT 1 CHECK (settlement_status IN (1,2,3)),
 settlement_at TIMESTAMPTZ, settlement_pool BIGINT, settlement_paid BIGINT,
 is_demo BOOLEAN NOT NULL DEFAULT false,
 demo_pool_a BIGINT NOT NULL DEFAULT 0, demo_pool_b BIGINT NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(team_a_id,api_source_id) REFERENCES lolpick.lolpick_teams(id,api_source_id),
 FOREIGN KEY(team_b_id,api_source_id) REFERENCES lolpick.lolpick_teams(id,api_source_id),
 UNIQUE(api_source_id,external_match_id), UNIQUE(id,season_id),
 CHECK (team_a_id IS NULL OR team_b_id IS NULL OR team_a_id <> team_b_id),
 CHECK ((score_a IS NULL AND score_b IS NULL) OR
  (score_a IS NOT NULL AND score_b IS NOT NULL AND score_a >= 0 AND score_b >= 0)),
 CHECK ((result IS NULL AND result_recorded_at IS NULL) OR
  (result IS NOT NULL AND result_recorded_at IS NOT NULL)),
 CHECK (result IS NULL OR (result IN (1,2,3) AND status = 3 AND team_a_id IS NOT NULL AND team_b_id IS NOT NULL)
  OR (result = 4 AND status = 4)),
 CHECK ((settlement_status = 1 AND settlement_at IS NULL AND settlement_pool IS NULL AND settlement_paid IS NULL)
  OR (settlement_status IN (2,3) AND settlement_at IS NOT NULL
   AND settlement_pool IS NOT NULL AND settlement_paid IS NOT NULL
   AND settlement_pool BETWEEN 0 AND 9007199254740991
   AND settlement_paid BETWEEN 0 AND settlement_pool
   AND ((settlement_status = 2 AND status = 3 AND result IS NOT NULL AND result IN (1,2))
    OR (settlement_status = 3 AND result IS NOT NULL AND status IN (3,4) AND settlement_paid = settlement_pool)))),
 CHECK (demo_pool_a >= 0 AND demo_pool_b >= 0 AND demo_pool_a <= 9007199254740991 - demo_pool_b),
 CHECK (is_demo OR (demo_pool_a = 0 AND demo_pool_b = 0))
);
CREATE INDEX ix_matches_season_start ON lolpick.lolpick_matches(season_id,starts_at);
CREATE INDEX ix_matches_pending ON lolpick.lolpick_matches(season_id,id) WHERE settlement_status = 1;

CREATE TABLE lolpick.lolpick_cheer_contracts (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 member_id BIGINT NOT NULL REFERENCES lolpick.lolpick_members(id),
 season_id BIGINT NOT NULL REFERENCES lolpick.lolpick_seasons(id),
 team_id BIGINT NOT NULL REFERENCES lolpick.lolpick_teams(id),
 seal_revoked_at TIMESTAMPTZ, finalized_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(member_id,season_id), UNIQUE(id,member_id,season_id)
);

-- reason: 1=season_open,2=signup,3=attendance,4=reward,
--         5=bet_stake,6=bet_payout,7=bet_refund,8=admin_adjustment
-- A reason=5 row IS the wager. There is no bets table.
CREATE TABLE lolpick.lolpick_point_transactions (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 member_id BIGINT NOT NULL REFERENCES lolpick.lolpick_members(id),
 season_id BIGINT NOT NULL REFERENCES lolpick.lolpick_seasons(id),
 match_id BIGINT,
 reason SMALLINT NOT NULL CHECK (reason IN (1,2,3,4,5,6,7,8)),
 amount BIGINT NOT NULL CHECK (amount BETWEEN -9007199254740991 AND 9007199254740991),
 balance_after BIGINT NOT NULL CHECK (balance_after BETWEEN 0 AND 9007199254740991),
 related_entry_id BIGINT REFERENCES lolpick.lolpick_point_transactions(id),
 selected_side SMALLINT,
 contract_id BIGINT, cheer_action SMALLINT,
 idempotency_key_hash BYTEA NOT NULL UNIQUE CHECK (octet_length(idempotency_key_hash) = 32),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(match_id,season_id) REFERENCES lolpick.lolpick_matches(id,season_id),
 FOREIGN KEY(contract_id,member_id,season_id)
  REFERENCES lolpick.lolpick_cheer_contracts(id,member_id,season_id),
 CHECK ((reason = 1 AND amount >= 0 AND match_id IS NULL) OR (reason <> 1 AND amount <> 0)),
 CHECK ((reason = 5 AND amount < 0) OR (reason IN (2,3,4,6,7) AND amount > 0) OR reason IN (1,8)),
 CHECK ((reason = 5 AND match_id IS NOT NULL AND selected_side IS NOT NULL
   AND selected_side IN (1,2) AND related_entry_id IS NULL)
  OR (reason IN (6,7) AND match_id IS NOT NULL AND selected_side IS NULL AND related_entry_id IS NOT NULL)
  OR (reason NOT IN (5,6,7) AND selected_side IS NULL AND related_entry_id IS NULL)),
 CHECK ((reason = 5 AND
   ((contract_id IS NULL AND cheer_action IS NULL) OR
    (contract_id IS NOT NULL AND cheer_action IS NOT NULL AND cheer_action IN (1,2))))
  OR (reason <> 5 AND contract_id IS NULL AND cheer_action IS NULL)),
 CHECK (related_entry_id IS NULL OR related_entry_id <> id)
);
CREATE UNIQUE INDEX uq_points_season_open ON lolpick.lolpick_point_transactions(member_id,season_id) WHERE reason = 1;
CREATE UNIQUE INDEX uq_points_one_wager ON lolpick.lolpick_point_transactions(member_id,match_id) WHERE reason = 5;
CREATE UNIQUE INDEX uq_points_one_final ON lolpick.lolpick_point_transactions(related_entry_id) WHERE reason IN (6,7);
CREATE INDEX ix_points_member_season_id ON lolpick.lolpick_point_transactions(member_id,season_id,id);
CREATE INDEX ix_points_member_history ON lolpick.lolpick_point_transactions(member_id,season_id,created_at);
CREATE INDEX ix_points_match_reason ON lolpick.lolpick_point_transactions(match_id,reason,member_id) WHERE match_id IS NOT NULL;

CREATE TABLE lolpick.lolpick_system_logs (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id > 0),
 season_id BIGINT REFERENCES lolpick.lolpick_seasons(id),
 actor_member_id BIGINT REFERENCES lolpick.lolpick_members(id),
 event_code SMALLINT NOT NULL CHECK (event_code IN (1,2,3,10,11,12,20,21,22,23,24,25,26,30,31,32,33,34,35,36,37,900)),
 outcome SMALLINT NOT NULL CHECK (outcome IN (1,2,3)),
 severity SMALLINT NOT NULL CHECK (severity IN (1,2,3)),
 source SMALLINT NOT NULL CHECK (source IN (1,2,3,4)),
 request_id UUID,
 event_key_hash BYTEA CHECK (event_key_hash IS NULL OR octet_length(event_key_hash) = 32),
 route_code SMALLINT CHECK (route_code BETWEEN 0 AND 19),
 http_method SMALLINT CHECK (http_method BETWEEN 1 AND 8),
 status_code SMALLINT CHECK (status_code BETWEEN 100 AND 599),
 phase SMALLINT CHECK (phase IN (1,2,3)),
 duration_ms INTEGER CHECK (duration_ms >= 0),
 error_code VARCHAR(80), message VARCHAR(256),
 context JSONB CHECK (context IS NULL OR
  (jsonb_typeof(context) = 'object' AND octet_length(context::text) <= 1024
   AND context - ARRAY['match_id','entry_id','count','error_type','inserted','updated','skipped',
    'conflicts','series_id','pool','paid','removed_remainder','error_code','settlement_blocked',
    'response_complete','reason','legacy_event_name','legacy_feature','legacy_action','archive_file']::text[] = '{}'::jsonb)),
 detail_sha256 BYTEA CHECK (detail_sha256 IS NULL OR octet_length(detail_sha256) = 32),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_logs_event_key ON lolpick.lolpick_system_logs(event_key_hash) WHERE event_key_hash IS NOT NULL;
CREATE INDEX ix_logs_request ON lolpick.lolpick_system_logs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX ix_logs_event_time ON lolpick.lolpick_system_logs(event_code,created_at);
CREATE INDEX ix_logs_time ON lolpick.lolpick_system_logs(created_at);

CREATE FUNCTION lolpick.guard_member() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
 IF TG_OP = 'INSERT' THEN
  IF NEW.type <> 1 OR NEW.current_points <> 0 OR NEW.current_season_id IS NOT NULL THEN
   RAISE EXCEPTION 'New members must be ordinary members with no season balance';
  END IF;
  NEW.created_at := clock_timestamp();
 ELSIF NEW.id IS DISTINCT FROM OLD.id OR NEW.auth_user_id IS DISTINCT FROM OLD.auth_user_id
  OR NEW.google_sub IS DISTINCT FROM OLD.google_sub OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
  RAISE EXCEPTION 'Member authentication identity is immutable';
 END IF;
 NEW.updated_at := clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER trg_member_identity BEFORE INSERT OR UPDATE ON lolpick.lolpick_members
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_member();

CREATE FUNCTION lolpick.guard_session() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
 IF TG_OP = 'INSERT' THEN
  NEW.create_dt := clock_timestamp();
  NEW.expires_at := NEW.create_dt + INTERVAL '6 hours';
  NEW.status := 1; NEW.revoked_at := NULL; NEW.status_changed_at := NEW.create_dt;
 ELSE
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.auth_session_id IS DISTINCT FROM OLD.auth_session_id
   OR NEW.member_id IS DISTINCT FROM OLD.member_id OR NEW.create_dt IS DISTINCT FROM OLD.create_dt
   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
   OR (OLD.status <> 1 AND NEW.status IS DISTINCT FROM OLD.status) THEN
   RAISE EXCEPTION 'Session identity, lifetime and terminal state are immutable';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
   NEW.status_changed_at := clock_timestamp();
   NEW.revoked_at := CASE WHEN NEW.status = 3 THEN NEW.status_changed_at ELSE NULL END;
  ELSIF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at OR NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
   RAISE EXCEPTION 'Session transition clocks are server managed';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_session_clock BEFORE INSERT OR UPDATE ON lolpick.lolpick_member_sessions
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_session();

CREATE FUNCTION lolpick.guard_team_identity() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.api_source_id IS DISTINCT FROM OLD.api_source_id
  OR NEW.external_team_id IS DISTINCT FROM OLD.external_team_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
  RAISE EXCEPTION 'Team identity is immutable; display fields can change';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_team_identity BEFORE UPDATE ON lolpick.lolpick_teams
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_team_identity();

CREATE FUNCTION lolpick.guard_contract() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE v_provider smallint; v_team_provider smallint;
BEGIN
 IF TG_OP = 'UPDATE' THEN
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.member_id IS DISTINCT FROM OLD.member_id
   OR NEW.season_id IS DISTINCT FROM OLD.season_id OR NEW.team_id IS DISTINCT FROM OLD.team_id
   OR NEW.created_at IS DISTINCT FROM OLD.created_at
   OR (OLD.seal_revoked_at IS NOT NULL AND NEW.seal_revoked_at IS DISTINCT FROM OLD.seal_revoked_at)
   OR (OLD.finalized_at IS NOT NULL AND NEW.finalized_at IS DISTINCT FROM OLD.finalized_at) THEN
   RAISE EXCEPTION 'Contract identity and completed transitions are immutable';
  END IF;
 ELSE
  SELECT api_source_id INTO v_provider FROM lolpick.lolpick_seasons WHERE id = NEW.season_id;
  SELECT api_source_id INTO v_team_provider FROM lolpick.lolpick_teams WHERE id = NEW.team_id;
  IF v_provider IS NOT NULL AND v_provider IS DISTINCT FROM v_team_provider THEN
   RAISE EXCEPTION 'Contract team belongs to another provider';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_contract_identity BEFORE INSERT OR UPDATE ON lolpick.lolpick_cheer_contracts
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_contract();

CREATE FUNCTION lolpick.guard_match() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE v_pool numeric; v_paid numeric; v_winner_count bigint; v_provider smallint; v_season_demo boolean;
BEGIN
 SELECT api_source_id,is_demo INTO v_provider,v_season_demo FROM lolpick.lolpick_seasons WHERE id = NEW.season_id;
 IF (v_provider IS NOT NULL AND NEW.api_source_id IS DISTINCT FROM v_provider)
  OR NEW.is_demo IS DISTINCT FROM v_season_demo THEN
  RAISE EXCEPTION 'Match provider/demo state differs from season';
 END IF;
 IF TG_OP = 'INSERT' THEN
  IF NEW.settlement_status <> 1 THEN RAISE EXCEPTION 'New matches must start unsettled'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.public_code IS DISTINCT FROM OLD.public_code
  OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN RAISE EXCEPTION 'Match public identity is immutable'; END IF;
 IF EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions WHERE match_id = OLD.id AND reason = 5)
  AND (NEW.team_a_id IS DISTINCT FROM OLD.team_a_id OR NEW.team_b_id IS DISTINCT FROM OLD.team_b_id
   OR NEW.season_id IS DISTINCT FROM OLD.season_id OR NEW.api_source_id IS DISTINCT FROM OLD.api_source_id
   OR NEW.external_match_id IS DISTINCT FROM OLD.external_match_id OR NEW.is_demo IS DISTINCT FROM OLD.is_demo) THEN
  RAISE EXCEPTION 'Wager-funded match identity and team slots are immutable';
 END IF;
 IF (OLD.settlement_status <> 1 OR EXISTS(
   SELECT 1 FROM lolpick.lolpick_point_transactions WHERE match_id = OLD.id AND reason IN (6,7)))
  AND (NEW.result IS DISTINCT FROM OLD.result OR NEW.status IS DISTINCT FROM OLD.status
   OR NEW.score_a IS DISTINCT FROM OLD.score_a OR NEW.score_b IS DISTINCT FROM OLD.score_b
   OR NEW.result_recorded_at IS DISTINCT FROM OLD.result_recorded_at) THEN
  RAISE EXCEPTION 'Financially posted match outcome is immutable';
 END IF;
 IF OLD.settlement_status <> 1 AND
  (NEW.settlement_status IS DISTINCT FROM OLD.settlement_status OR NEW.settlement_at IS DISTINCT FROM OLD.settlement_at
   OR NEW.settlement_pool IS DISTINCT FROM OLD.settlement_pool OR NEW.settlement_paid IS DISTINCT FROM OLD.settlement_paid) THEN
  RAISE EXCEPTION 'Completed settlement is immutable';
 END IF;
 IF OLD.settlement_status = 1 AND NEW.settlement_status IN (2,3) THEN
  IF NEW.result IS NULL OR NEW.result NOT IN (1,2,3,4) THEN RAISE EXCEPTION 'Settlement requires a known result'; END IF;
  IF (NEW.issue_flags & 4064) <> 0 THEN RAISE EXCEPTION 'Resolve critical provider conflicts before settlement'; END IF;
  IF NEW.is_demo THEN
   IF NEW.settlement_pool <> 0 OR NEW.settlement_paid <> 0 THEN RAISE EXCEPTION 'Demo matches cannot post real points'; END IF;
  ELSE
   SELECT COALESCE(sum(-amount::numeric),0) INTO v_pool
    FROM lolpick.lolpick_point_transactions WHERE match_id = NEW.id AND reason = 5;
   SELECT COALESCE(sum(amount::numeric),0) INTO v_paid
    FROM lolpick.lolpick_point_transactions WHERE match_id = NEW.id AND reason IN (6,7);
   IF NEW.settlement_pool IS DISTINCT FROM v_pool OR NEW.settlement_paid IS DISTINCT FROM v_paid THEN
    RAISE EXCEPTION 'Settlement totals differ from ledger';
   END IF;
   SELECT count(*) INTO v_winner_count FROM lolpick.lolpick_point_transactions
    WHERE match_id = NEW.id AND reason = 5 AND selected_side = NEW.result;
   IF NEW.settlement_status = 2 THEN
    IF NEW.result NOT IN (1,2) OR NEW.status <> 3 THEN RAISE EXCEPTION 'Winning settlement requires a winner'; END IF;
    IF v_pool > 0 AND v_winner_count = 0 THEN RAISE EXCEPTION 'No winning wagers: refund funded wagers'; END IF;
    IF EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions WHERE match_id = NEW.id AND reason = 7) THEN
     RAISE EXCEPTION 'Winning settlement contains a refund';
    END IF;
    IF EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions w WHERE w.match_id = NEW.id AND w.reason = 5
     AND w.selected_side = NEW.result AND NOT EXISTS(
      SELECT 1 FROM lolpick.lolpick_point_transactions p WHERE p.related_entry_id = w.id AND p.reason = 6)) THEN
     RAISE EXCEPTION 'A winning wager has no payout';
    END IF;
   ELSE
    IF NEW.result IN (1,2) AND v_winner_count > 0 THEN RAISE EXCEPTION 'Winning wagers exist: use payout settlement'; END IF;
    IF EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions WHERE match_id = NEW.id AND reason = 6) THEN
     RAISE EXCEPTION 'Refund settlement contains a payout';
    END IF;
    IF EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions w WHERE w.match_id = NEW.id AND w.reason = 5
     AND NOT EXISTS(SELECT 1 FROM lolpick.lolpick_point_transactions p WHERE p.related_entry_id = w.id AND p.reason = 7)) THEN
     RAISE EXCEPTION 'A wager has no refund';
    END IF;
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_match_financial_guard BEFORE INSERT OR UPDATE ON lolpick.lolpick_matches
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_match();

-- Trigger owner must be the trusted migration owner, not the runtime login.
-- Runtime cannot update member balances directly. This function computes both
-- the season ledger balance_after and last-touched member cache atomically.
CREATE FUNCTION lolpick.post_point_entry() RETURNS trigger LANGUAGE plpgsql
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
CREATE TRIGGER trg_points_post BEFORE INSERT OR UPDATE OR DELETE ON lolpick.lolpick_point_transactions
 FOR EACH ROW EXECUTE FUNCTION lolpick.post_point_entry();
CREATE TRIGGER trg_points_no_truncate BEFORE TRUNCATE ON lolpick.lolpick_point_transactions
 FOR EACH STATEMENT EXECUTE FUNCTION lolpick.post_point_entry();


-- Sole runtime financial write interface: one plain INSERT, no ON CONFLICT.
-- Direct INSERT on the ledger is NOT granted to the runtime role. This is
-- essential: a BEFORE trigger must not update balances for a skipped insert.
CREATE FUNCTION lolpick.lock_members(p_ids bigint[])
 RETURNS SETOF lolpick.lolpick_members LANGUAGE plpgsql
 SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_count bigint;
BEGIN
 IF p_ids IS NULL OR EXISTS(SELECT 1 FROM unnest(p_ids) AS ids(id) WHERE id IS NULL OR id <= 0) THEN
  RAISE EXCEPTION 'Invalid member lock identities';
 END IF;
 RETURN QUERY SELECT m.* FROM lolpick.lolpick_members m WHERE m.id = ANY(p_ids) ORDER BY m.id FOR UPDATE;
 GET DIAGNOSTICS v_count = ROW_COUNT;
 IF v_count <> (SELECT count(DISTINCT id) FROM unnest(p_ids) AS ids(id)) THEN
  RAISE EXCEPTION 'Unknown member lock identity';
 END IF;
END $$;

CREATE FUNCTION lolpick.post_points(
 p_member_id bigint,p_season_id bigint,p_reason smallint,p_amount bigint,p_key bytea,
 p_match_id bigint DEFAULT NULL,p_related_entry_id bigint DEFAULT NULL,
 p_selected_side smallint DEFAULT NULL,p_contract_id bigint DEFAULT NULL,p_cheer_action smallint DEFAULT NULL
) RETURNS lolpick.lolpick_point_transactions LANGUAGE plpgsql
 SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_old lolpick.lolpick_point_transactions%ROWTYPE; v_new lolpick.lolpick_point_transactions%ROWTYPE;
BEGIN
 IF p_key IS NULL OR octet_length(p_key) <> 32 THEN RAISE EXCEPTION 'Request digest must contain 32 bytes'; END IF;
 -- All writers use season -> match -> member -> contract lock order.
 PERFORM 1 FROM lolpick.lolpick_seasons WHERE id = p_season_id FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Unknown season'; END IF;
 IF p_match_id IS NOT NULL THEN
  PERFORM 1 FROM lolpick.lolpick_matches WHERE id = p_match_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unknown match'; END IF;
 END IF;
 PERFORM 1 FROM lolpick.lolpick_members WHERE id = p_member_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Unknown member'; END IF;
 SELECT * INTO v_old FROM lolpick.lolpick_point_transactions WHERE idempotency_key_hash = p_key;
 IF FOUND THEN
  IF (v_old.member_id,v_old.season_id,v_old.reason,v_old.amount,v_old.match_id,
      v_old.related_entry_id,v_old.selected_side,v_old.contract_id,v_old.cheer_action)
    IS DISTINCT FROM (p_member_id,p_season_id,p_reason,p_amount,p_match_id,
      p_related_entry_id,p_selected_side,p_contract_id,p_cheer_action) THEN
   RAISE EXCEPTION 'Request key was reused with a different payload';
  END IF;
  RETURN v_old;
 END IF;
 INSERT INTO lolpick.lolpick_point_transactions
  (member_id,season_id,reason,amount,match_id,related_entry_id,selected_side,contract_id,cheer_action,idempotency_key_hash)
 VALUES(p_member_id,p_season_id,p_reason,p_amount,p_match_id,p_related_entry_id,
  p_selected_side,p_contract_id,p_cheer_action,p_key)
 RETURNING * INTO v_new;
 RETURN v_new;
END $$;

CREATE FUNCTION lolpick.guard_season_finish() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.public_code IS DISTINCT FROM OLD.public_code THEN RAISE EXCEPTION 'Season identity is immutable'; END IF;
 IF OLD.status = 3 AND NEW.status IS DISTINCT FROM OLD.status THEN RAISE EXCEPTION 'Finished season cannot reactivate'; END IF;
 IF (NEW.api_source_id IS DISTINCT FROM OLD.api_source_id OR NEW.is_demo IS DISTINCT FROM OLD.is_demo)
  AND EXISTS(SELECT 1 FROM lolpick.lolpick_matches WHERE season_id = OLD.id) THEN
  RAISE EXCEPTION 'A season with matches cannot change provider/demo binding';
 END IF;
 IF NEW.status = 3 AND OLD.status <> 3 AND EXISTS(
  SELECT 1 FROM lolpick.lolpick_matches m JOIN lolpick.lolpick_point_transactions w ON w.match_id = m.id
   WHERE m.season_id = OLD.id AND m.settlement_status = 1 AND w.reason = 5) THEN
  RAISE EXCEPTION 'Settle funded matches before finishing the season';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_season_finish BEFORE UPDATE ON lolpick.lolpick_seasons
 FOR EACH ROW EXECUTE FUNCTION lolpick.guard_season_finish();

-- Query projection only: no extra storage or mutable bet status.
CREATE VIEW lolpick.lolpick_wagers WITH (security_barrier = true) AS
 SELECT w.id,w.member_id,w.season_id,w.match_id,w.selected_side,-w.amount AS amount,
  w.contract_id,w.cheer_action,w.created_at,
  CASE WHEN m.settlement_status = 1 THEN 1 WHEN m.settlement_status = 3 THEN 4
       WHEN w.selected_side = m.result THEN 2 ELSE 3 END::smallint AS status,
  CASE WHEN m.settlement_status = 1 THEN NULL WHEN m.settlement_status = 3 THEN -w.amount
       WHEN w.selected_side = m.result THEN p.amount ELSE 0 END::bigint AS payout,
  m.settlement_at AS settled_at
 FROM lolpick.lolpick_point_transactions w
 JOIN lolpick.lolpick_matches m ON m.id = w.match_id
 LEFT JOIN lolpick.lolpick_point_transactions p ON p.related_entry_id = w.id AND p.reason IN (6,7)
 WHERE w.reason = 5;

COMMENT ON COLUMN lolpick.lolpick_members.current_points IS 'Last-touched season cache only; use the member+season ledger for balances.';
COMMENT ON COLUMN lolpick.lolpick_members.current_season_id IS 'Cache scope, not an exclusive season membership or UI selection.';
COMMENT ON COLUMN lolpick.lolpick_members.type IS '1=member,2=admin. Runtime cannot insert/update this column.';
COMMENT ON COLUMN lolpick.lolpick_teams.name IS 'Current display name; historical fixtures also display the latest name.';
COMMENT ON COLUMN lolpick.lolpick_matches.starts_at IS 'NULL=unannounced. Store fixture, prohibit wagering until confirmed.';
COMMENT ON COLUMN lolpick.lolpick_matches.team_a_id IS 'NULL=TBD; never create a fictitious placeholder team.';
COMMENT ON COLUMN lolpick.lolpick_matches.status IS '1=scheduled,2=live,3=finished,4=cancelled';
COMMENT ON COLUMN lolpick.lolpick_matches.result IS '1=A win,2=B win,3=draw,4=cancelled';
COMMENT ON COLUMN lolpick.lolpick_matches.provider_status IS '1=not_started,2=postponed,3=running,4=finished,5=canceled';
COMMENT ON COLUMN lolpick.lolpick_matches.settlement_status IS '1=pending,2=settled,3=refunded. Totals mandatory when complete.';
COMMENT ON COLUMN lolpick.lolpick_point_transactions.related_entry_id IS 'Payout/refund references an immutable reason=5 row in this table.';
COMMENT ON COLUMN lolpick.lolpick_point_transactions.balance_after IS 'Server trigger computes balance. Never update balances again in application code.';
COMMENT ON VIEW lolpick.lolpick_wagers IS 'Derived wager history, no table storage. Pending partial postings remain pending until match settlement commits.';

DO $$
DECLARE t text; r text;
BEGIN
 FOREACH t IN ARRAY ARRAY['api_sources','seasons','members','member_sessions','teams','matches','cheer_contracts','point_transactions','system_logs'] LOOP
  EXECUTE format('ALTER TABLE lolpick.%I ENABLE ROW LEVEL SECURITY','lolpick_' || t);
 END LOOP;
 REVOKE ALL ON SCHEMA lolpick FROM PUBLIC;
 REVOKE ALL ON ALL TABLES IN SCHEMA lolpick FROM PUBLIC;
 REVOKE ALL ON ALL SEQUENCES IN SCHEMA lolpick FROM PUBLIC;
 REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lolpick FROM PUBLIC;
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname = r) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA lolpick FROM %I',r);
   EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA lolpick FROM %I',r);
   EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA lolpick FROM %I',r);
   EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lolpick FROM %I',r);
  END IF;
 END LOOP;
END $$;
ALTER DEFAULT PRIVILEGES IN SCHEMA lolpick REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
COMMENT ON SCHEMA lolpick IS 'LoLPICK v7 design: 9 business tables, unified ledger; runtime/migration integration pending';
COMMIT;

