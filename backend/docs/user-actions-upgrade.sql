BEGIN;
DO $$ BEGIN
 IF (SELECT array_agg(version_num) FROM lolpick.lolpick_alembic_version) IS DISTINCT FROM ARRAY['0008_native_session_lookup']::varchar[] THEN
  RAISE EXCEPTION 'Expected installed revision 0008_native_session_lookup; do not replay this patch.';
 END IF;
END $$;
-- Add the two authenticated action paths to the bounded numeric audit routes.
-- Run under the migration owner, never the restricted API role.
ALTER TABLE lolpick.lolpick_system_logs
 DROP CONSTRAINT lolpick_system_logs_route_code_check;
ALTER TABLE lolpick.lolpick_system_logs
 ADD CONSTRAINT lolpick_system_logs_route_code_check CHECK (route_code BETWEEN 0 AND 16);

UPDATE lolpick.lolpick_alembic_version SET version_num='0009_user_actions' WHERE version_num='0008_native_session_lookup';
COMMIT;
