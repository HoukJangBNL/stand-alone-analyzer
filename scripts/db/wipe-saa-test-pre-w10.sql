-- W10-A pre-flight: wipe project-scoped rows so 0004_w10_projects can apply
-- as pure DDL. Owner runs this manually against saa_test BEFORE alembic.
-- Production RDS is empty — skip this on prod.
--
-- DOES NOT TRUNCATE: users, models, materials, usage_events.

BEGIN;
TRUNCATE TABLE
    flake_curations,
    flake_analyses,
    domain_assignments,
    domain_groups,
    domain_analyses,
    flakes,
    domains,
    runs,
    analyses,
    upload_items,
    upload_sessions,
    images,
    scans,
    project_users
RESTART IDENTITY CASCADE;
COMMIT;
