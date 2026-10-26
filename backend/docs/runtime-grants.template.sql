-- LoLPICK v7 trusted-backend permissions TEMPLATE.
-- Apply only after v7 runtime integration and schema validation.
-- Replace lolpick_runtime with the existing dedicated backend login.
-- Do not use an owner, superuser, BYPASSRLS role, or owner-role member.
BEGIN;
DO $$
DECLARE t text; v_owner oid; v_role oid;
BEGIN
 SELECT oid INTO v_role FROM pg_roles WHERE rolname = 'lolpick_runtime' AND rolcanlogin
  AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication;
 IF v_role IS NULL THEN RAISE EXCEPTION 'Use a non-superuser, non-BYPASSRLS backend login'; END IF;
 FOR v_owner IN
  SELECT c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='lolpick'
  UNION SELECT nspowner FROM pg_namespace WHERE nspname='lolpick'
  UNION SELECT p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='lolpick'
 LOOP
  IF pg_has_role(v_role,v_owner,'MEMBER') THEN RAISE EXCEPTION 'Backend must not be a schema/table/function owner-role member'; END IF;
 END LOOP;
 GRANT USAGE ON SCHEMA lolpick TO lolpick_runtime;
 REVOKE ALL ON ALL TABLES IN SCHEMA lolpick FROM lolpick_runtime;
 REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lolpick FROM lolpick_runtime;
 GRANT SELECT ON ALL TABLES IN SCHEMA lolpick TO lolpick_runtime;
 -- Read the migration marker even when RLS was enabled by the remote bootstrap.
 ALTER TABLE lolpick.lolpick_alembic_version ENABLE ROW LEVEL SECURITY;
 DROP POLICY IF EXISTS backend_v7_version ON lolpick.lolpick_alembic_version;
 CREATE POLICY backend_v7_version ON lolpick.lolpick_alembic_version
  FOR SELECT TO lolpick_runtime USING (true);
 -- auth.sessions uses RLS. Grant only the private matching-session lookup;
 -- never grant direct auth table access or change Supabase-managed RLS.
 GRANT EXECUTE ON FUNCTION lolpick.read_native_session(uuid,uuid) TO lolpick_runtime;
 GRANT USAGE ON ALL SEQUENCES IN SCHEMA lolpick TO lolpick_runtime;
 GRANT INSERT,UPDATE ON lolpick.lolpick_api_sources,lolpick.lolpick_seasons,
  lolpick.lolpick_teams,lolpick.lolpick_matches TO lolpick_runtime;
 GRANT INSERT ON lolpick.lolpick_cheer_contracts,lolpick.lolpick_system_logs TO lolpick_runtime;
 GRANT UPDATE(seal_revoked_at,finalized_at) ON lolpick.lolpick_cheer_contracts TO lolpick_runtime;
 GRANT INSERT(auth_user_id,google_sub) ON lolpick.lolpick_members TO lolpick_runtime;
 GRANT INSERT(auth_session_id,member_id) ON lolpick.lolpick_member_sessions TO lolpick_runtime;
 GRANT UPDATE(status) ON lolpick.lolpick_member_sessions TO lolpick_runtime;
 -- NEVER GRANT direct ledger INSERT/UPDATE/DELETE or member type/balance UPDATE.
 GRANT EXECUTE ON FUNCTION lolpick.lock_members(bigint[]) TO lolpick_runtime;
 GRANT EXECUTE ON FUNCTION lolpick.post_points(bigint,bigint,smallint,bigint,bytea,bigint,bigint,smallint,bigint,smallint)
  TO lolpick_runtime;
 FOREACH t IN ARRAY ARRAY['api_sources','seasons','members','member_sessions','teams','matches','cheer_contracts','point_transactions','system_logs'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS backend_v7 ON lolpick.%I','lolpick_' || t);
  EXECUTE format('CREATE POLICY backend_v7 ON lolpick.%I TO lolpick_runtime USING (true) WITH CHECK (true)','lolpick_' || t);
 END LOOP;
 IF has_column_privilege('lolpick_runtime','lolpick.lolpick_members','type','UPDATE')
  OR has_column_privilege('lolpick_runtime','lolpick.lolpick_members','type','INSERT')
  OR has_column_privilege('lolpick_runtime','lolpick.lolpick_members','current_points','UPDATE')
  OR has_table_privilege('lolpick_runtime','lolpick.lolpick_point_transactions','INSERT') THEN
  RAISE EXCEPTION 'Unsafe inherited runtime privileges: remove owner/write grants before applying';
 END IF;
END $$;
COMMIT;
-- Backend policies trust the SERVER role, not each logged-in user.
-- Every API must independently verify the Google session, member ownership,
-- administrator type and action-specific permissions.
-- Operator type changes use a separate privileged login and an atomic audit log.
-- Maintenance/retention DELETE is not granted to the backend role.
