-- Read-only. Includes all schemas: Auth and application tables share the quota.
BEGIN TRANSACTION READ ONLY;
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size,
       pg_database_size(current_database()) AS database_bytes;

SELECT n.nspname AS schema_name, c.relname AS table_name,
       pg_size_pretty(pg_table_size(c.oid)) AS table_and_toast,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
ORDER BY pg_total_relation_size(c.oid) DESC;

SELECT schemaname, relname AS table_name, indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size, idx_scan
FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC;
COMMIT;
