-- Called only by the trusted backend after Supabase validates the exact token.
-- No grants or policies are changed in the Supabase-managed auth schema.
CREATE FUNCTION lolpick.read_native_session(p_session_id uuid,p_user_id uuid)
 RETURNS TABLE(user_id uuid,created_at timestamptz)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $lookup$
BEGIN
 RETURN QUERY
 SELECT s.user_id,s.created_at FROM auth.sessions AS s
 WHERE s.id=p_session_id AND s.user_id=p_user_id
  AND (s.not_after IS NULL OR s.not_after > clock_timestamp());
END $lookup$;
REVOKE ALL ON FUNCTION lolpick.read_native_session(uuid,uuid) FROM PUBLIC;
DO $revoke$
DECLARE r text;
BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON FUNCTION lolpick.read_native_session(uuid,uuid) FROM %I',r);
  END IF;
 END LOOP;
END $revoke$;
