-- Read-only review queries: use the actual runtime DATABASE_URL account.
-- These statements inspect configuration; they do not create/grant a role.
SELECT current_user, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb
FROM pg_roles r WHERE r.rolname = current_user;

SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity,
       has_table_privilege(current_user, c.oid, 'SELECT') AS can_select,
       has_table_privilege(current_user, c.oid, 'INSERT') AS can_insert,
       has_table_privilege(current_user, c.oid, 'UPDATE') AS can_update,
       has_table_privilege(current_user, c.oid, 'DELETE') AS can_delete
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='lolpick' AND c.relkind='r' ORDER BY c.relname;

SELECT schemaname, tablename, policyname, roles, cmd
FROM pg_policies WHERE schemaname='lolpick' ORDER BY tablename, policyname;

SELECT has_table_privilege(current_user, 'auth.sessions', 'SELECT') AS can_read_native_sessions;
SHOW statement_timeout;
SHOW lock_timeout;
SHOW idle_in_transaction_session_timeout;
-- Connection timeout settings must be inspected through app runtime connections,
-- not only this standalone psql session.
