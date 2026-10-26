"""Read-only checks for the deployed v7 schema and the actual application role."""
from sqlalchemy import text
from uuid import UUID
from app.db.schema import REVISION, TABLE_SPECS


class DatabaseReadinessError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def verify_runtime(db, *, require_restricted_role=False):
    revision = db.execute(
        text("SELECT version_num FROM lolpick.lolpick_alembic_version")
    ).scalar_one_or_none()
    if revision != REVISION:
        raise DatabaseReadinessError("schema_not_ready")
    # Catalog inspection reveals no member/provider data and performs no writes.
    tables = db.execute(text("""
        SELECT c.relname, c.relrowsecurity,
               has_table_privilege(current_user,c.oid,'SELECT') AS readable,
               EXISTS (
                 SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polcmd='*'
                 AND p.polpermissive AND pg_get_expr(p.polqual,c.oid)='true'
                 AND pg_get_expr(p.polwithcheck,c.oid)='true'
                 AND EXISTS (SELECT 1 FROM unnest(p.polroles) r(role_id)
                     WHERE r.role_id=0 OR pg_has_role(current_user,r.role_id,'USAGE'))
               ) AS backend_policy
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='lolpick' AND c.relkind='r'
    """)).mappings().all()
    found = {r["relname"]: r for r in tables}
    for spec in TABLE_SPECS:
        row = found.get("lolpick_" + spec["name"])
        if row is None or not row["relrowsecurity"] or not row["readable"]:
            raise DatabaseReadinessError("schema_not_ready")
        if require_restricted_role and not row["backend_policy"]:
            raise DatabaseReadinessError("runtime_permissions_not_ready")
    flags = db.execute(text("""
        SELECT
          has_function_privilege(current_user,'pg_catalog.pg_advisory_xact_lock(bigint)','EXECUTE')
          AND has_function_privilege(current_user,'pg_catalog.pg_advisory_xact_lock_shared(bigint)','EXECUTE')
          AND has_function_privilege(current_user,'pg_catalog.pg_advisory_xact_lock(integer,integer)','EXECUTE')
          AND has_function_privilege(current_user,'pg_catalog.hashtext(text)','EXECUTE') AS registration_locks,
          COALESCE(has_function_privilege(current_user,
            to_regprocedure('lolpick.lock_members(bigint[])'),'EXECUTE'),false) AS lock_members,
          COALESCE(has_function_privilege(current_user,
            to_regprocedure('lolpick.post_points(bigint,bigint,smallint,bigint,bytea,bigint,bigint,smallint,bigint,smallint)'),
            'EXECUTE'),false) AS post_points,
          COALESCE(has_function_privilege(current_user,
            to_regprocedure('lolpick.read_native_session(uuid,uuid)'),'EXECUTE'),false) AS native_sessions,
          (SELECT NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
             FROM pg_roles WHERE rolname=current_user)
          AND NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='lolpick' AND pg_has_role(current_user,c.relowner,'MEMBER')
          ) AND NOT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='lolpick' AND pg_has_role(current_user,p.proowner,'MEMBER')
          ) AND NOT EXISTS (
            SELECT 1 FROM pg_namespace n
            WHERE n.nspname='lolpick' AND pg_has_role(current_user,n.nspowner,'MEMBER')
          )
          AND NOT (
            has_column_privilege(current_user,'lolpick.lolpick_members','type','INSERT')
            OR has_column_privilege(current_user,'lolpick.lolpick_members','type','UPDATE')
            OR has_column_privilege(current_user,'lolpick.lolpick_members','current_points','UPDATE')
            OR has_table_privilege(current_user,'lolpick.lolpick_point_transactions','INSERT')
            OR has_table_privilege(current_user,'lolpick.lolpick_point_transactions','UPDATE')
            OR has_table_privilege(current_user,'lolpick.lolpick_point_transactions','DELETE')
            OR has_table_privilege(current_user,'lolpick.lolpick_point_transactions','TRUNCATE')
          ) AS restricted_role
    """)).mappings().one()
    if not all(flags[k] for k in ("lock_members", "post_points", "native_sessions", "registration_locks")):
        raise DatabaseReadinessError("runtime_permissions_not_ready")
    if require_restricted_role and not flags["restricted_role"]:
        raise DatabaseReadinessError("runtime_role_unsafe")
    # Execute the same private lookup used by authentication with nil identities.
    # No auth rows are returned and no data or permissions are changed.
    db.execute(text("SELECT * FROM lolpick.read_native_session(:session,:user)"),
               {"session": UUID(int=0), "user": UUID(int=0)}).all()
    return revision
