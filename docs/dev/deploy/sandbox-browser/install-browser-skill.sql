-- Enable the preinstalled `browser` skill for an EXISTING org.
--
-- New orgs auto-install all preinstalled skills at registration, but orgs
-- created before the `browser` skill was added are not backfilled, so the agent
-- never sees it in its "Available skills" list and won't drive the browser.
-- This inserts the org-wide install (auto_bind=true => enabled for every
-- workspace in the org). It is the manual stand-in until a backfill job exists.
--
-- Usage:
--   psql "$DATABASE_URL" \
--     -v org="org-XXXX" -v installer="usr-XXXX" -v iid="osi-browser-XXXX" \
--     -f install-browser-skill.sql
--
-- :org       organization id to enable it for
-- :installer a user id in that org (FK installed_by_user_id)
-- :iid       a fresh public id for the install row (osi- prefix, <=20 chars)

INSERT INTO org_skill_installs
  (id, org_id, skill_id, installed_version, installed_by_user_id,
   installed_at, auto_bind, created_at, updated_at, workspace_id)
SELECT
  :'iid', :'org', s.id, sv.version, :'installer',
  now(), true, now(), now(), NULL
FROM skills s
JOIN skill_versions sv
  ON sv.skill_id = s.id AND sv.version = s.current_version
WHERE s.name = 'browser'
ON CONFLICT DO NOTHING;

-- Verify:
--   SELECT o.id, s.name, o.installed_version, o.auto_bind
--   FROM org_skill_installs o JOIN skills s ON s.id = o.skill_id
--   WHERE s.name = 'browser' AND o.org_id = :'org';
