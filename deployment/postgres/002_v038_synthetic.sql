-- Executable only in the disposable v0.3.8 synthetic staging database.
-- Run as aipo_migration, after the bootstrap creates the database and roles.
BEGIN;
CREATE SCHEMA aipo_auth;
CREATE SCHEMA aipo_data;
REVOKE ALL ON SCHEMA aipo_auth,aipo_data FROM PUBLIC;
CREATE TABLE aipo_auth.membership (
 tenant uuid NOT NULL, user_id uuid NOT NULL,
 role text NOT NULL CHECK(role IN ('owner','manager','auditor')),
 stores uuid[] NOT NULL DEFAULT '{}', active boolean NOT NULL DEFAULT true,
 expires timestamptz NOT NULL, PRIMARY KEY(tenant,user_id)
);
REVOKE ALL ON aipo_auth.membership FROM PUBLIC;
CREATE FUNCTION aipo_auth.can_access(t uuid,s uuid,operation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog AS $body$
 SELECT EXISTS (
  SELECT 1 FROM aipo_auth.membership m
  WHERE operation IN ('read','write','admin') AND m.tenant=t AND t=NULLIF(current_setting('app.tenant_id',true),'')::uuid
    AND m.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid
    AND m.active AND m.expires>statement_timestamp()
    AND (m.role='owner' OR s=ANY(m.stores))
    AND (operation='read' OR (operation='write' AND m.role IN ('owner','manager'))
         OR (operation='admin' AND m.role='owner'))
 )
$body$;
REVOKE ALL ON FUNCTION aipo_auth.can_access(uuid,uuid,text) FROM PUBLIC;
CREATE TABLE aipo_data.stores (
 tenant uuid NOT NULL, id uuid NOT NULL, label text NOT NULL,
 PRIMARY KEY(tenant,id)
);
CREATE TABLE aipo_data.reports (
 tenant uuid NOT NULL, id uuid NOT NULL, store uuid NOT NULL,
 source_hash text NOT NULL, synthetic_payload jsonb NOT NULL,
 PRIMARY KEY(tenant,id), FOREIGN KEY(tenant,store) REFERENCES aipo_data.stores(tenant,id)
);
ALTER TABLE aipo_data.stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE aipo_data.stores FORCE ROW LEVEL SECURITY;
ALTER TABLE aipo_data.reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE aipo_data.reports FORCE ROW LEVEL SECURITY;
CREATE POLICY stores_read ON aipo_data.stores FOR SELECT TO aipo_api,aipo_worker USING(aipo_auth.can_access(tenant,id,'read'));
CREATE POLICY stores_insert ON aipo_data.stores FOR INSERT TO aipo_api WITH CHECK(aipo_auth.can_access(tenant,id,'admin'));
CREATE POLICY stores_update ON aipo_data.stores FOR UPDATE TO aipo_api USING(aipo_auth.can_access(tenant,id,'admin')) WITH CHECK(aipo_auth.can_access(tenant,id,'admin'));
CREATE POLICY reports_read ON aipo_data.reports FOR SELECT TO aipo_api,aipo_worker USING(aipo_auth.can_access(tenant,store,'read'));
CREATE POLICY reports_insert ON aipo_data.reports FOR INSERT TO aipo_worker WITH CHECK(aipo_auth.can_access(tenant,store,'write'));
-- No UPDATE/DELETE policy: source reports immutable for runtime callers.
CREATE POLICY stores_backup ON aipo_data.stores FOR SELECT TO aipo_backup USING(true);
CREATE POLICY reports_backup ON aipo_data.reports FOR SELECT TO aipo_backup USING(true);
GRANT USAGE ON SCHEMA aipo_auth TO aipo_api,aipo_worker;
GRANT USAGE ON SCHEMA aipo_data TO aipo_api,aipo_worker,aipo_backup;
GRANT EXECUTE ON FUNCTION aipo_auth.can_access(uuid,uuid,text) TO aipo_api,aipo_worker;
GRANT SELECT ON aipo_data.stores,aipo_data.reports TO aipo_api,aipo_worker,aipo_backup;
GRANT INSERT, UPDATE (label) ON aipo_data.stores TO aipo_api;
GRANT INSERT ON aipo_data.reports TO aipo_worker;
COMMIT;
