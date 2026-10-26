-- Add the two authenticated action paths to the bounded numeric audit routes.
-- Run under the migration owner, never the restricted API role.
ALTER TABLE lolpick.lolpick_system_logs
 DROP CONSTRAINT lolpick_system_logs_route_code_check;
ALTER TABLE lolpick.lolpick_system_logs
 ADD CONSTRAINT lolpick_system_logs_route_code_check CHECK (route_code BETWEEN 0 AND 16);
