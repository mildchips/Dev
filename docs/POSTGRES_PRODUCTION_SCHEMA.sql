-- PROPOSAL ONLY: NOT APPLIED TO THE LOCAL SQLITE RELEASE.
-- PostgreSQL 16+ architecture skeleton. Authentication + RLS policies need
-- penetration testing before public use. Never accept tenant_id from browser JS.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 external_auth_subject text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS memberships (
 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 role text NOT NULL CHECK (role IN ('owner','manager','auditor')),
 PRIMARY KEY(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS stores (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 provider text NOT NULL CHECK (provider IN ('WB','Ozon')),
 name text NOT NULL,
 provider_seller_id text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS store_permissions (
 tenant_id uuid NOT NULL,
 store_id uuid NOT NULL,
 user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 permission text NOT NULL CHECK (permission IN ('view','manage')),
 PRIMARY KEY (store_id,user_id),
 FOREIGN KEY (tenant_id,store_id) REFERENCES stores(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS credentials_metadata (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL,
 store_id uuid NOT NULL,
 provider text NOT NULL CHECK (provider IN ('WB','Ozon')),
 external_kms_ref text NOT NULL,  -- reference, never plaintext API key
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','paused','revoked')),
 version integer NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,store_id) REFERENCES stores(tenant_id,id) ON DELETE CASCADE,
 UNIQUE (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS sync_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL,
 store_id uuid NOT NULL,
 credential_id uuid NOT NULL,
 endpoint_group text NOT NULL,
 cursor text,
 window_start timestamptz,
 window_end timestamptz,
 status text NOT NULL DEFAULT 'pending'
   CHECK (status IN ('pending','leased','done','retry','blocked','dead')),
 scheduled_at timestamptz NOT NULL,
 leased_until timestamptz,
 lease_token uuid,
 attempts integer NOT NULL DEFAULT 0,
 idempotency_key text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY (tenant_id,store_id) REFERENCES stores(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY (tenant_id,credential_id) REFERENCES credentials_metadata(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS sync_jobs_ready ON sync_jobs(status,scheduled_at);
CREATE INDEX IF NOT EXISTS sync_jobs_tenant_store ON sync_jobs(tenant_id,store_id,status);
CREATE TABLE IF NOT EXISTS integration_stop_flags (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 scope text NOT NULL CHECK (scope IN ('global','provider','tenant','store','credential')),
 target text NOT NULL,
 stopped boolean NOT NULL DEFAULT false,
 epoch bigint NOT NULL DEFAULT 0,
 changed_by uuid,
 reason text,
 changed_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(scope,target)
);
CREATE TABLE IF NOT EXISTS integration_security_audit (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 created_at timestamptz NOT NULL DEFAULT now(),
 actor_id uuid,
 action text NOT NULL,
 target_type text NOT NULL,
 target_id text NOT NULL,
 reason text,
 request_id uuid
);
-- Append-only source identities. Reconciliation is designed separately.
CREATE TABLE IF NOT EXISTS marketplace_source_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id uuid NOT NULL,
 store_id uuid NOT NULL,
 provider text NOT NULL,
 source_endpoint text NOT NULL,
 provider_event_id text,
 event_hash text NOT NULL,
 raw_object_ref text NOT NULL,
 source_event_time timestamptz,
 imported_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,store_id) REFERENCES stores(tenant_id,id) ON DELETE CASCADE,
 UNIQUE(tenant_id,store_id,source_endpoint,event_hash)
);
CREATE INDEX IF NOT EXISTS source_events_tenant_time ON marketplace_source_events(tenant_id,store_id,source_event_time);

-- RLS must also be enforced in application DB role / connection pool policy.
ALTER TABLE stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE stores FORCE ROW LEVEL SECURITY;
ALTER TABLE credentials_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE credentials_metadata FORCE ROW LEVEL SECURITY;
ALTER TABLE sync_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE marketplace_source_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_source_events FORCE ROW LEVEL SECURITY;

-- EXAMPLE ONLY. app.tenant_id may only be set server-side from verified identity
-- within a transaction; never use raw user-controlled query arguments.
CREATE POLICY tenant_stores ON stores USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_credentials ON credentials_metadata USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_jobs ON sync_jobs USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_events ON marketplace_source_events USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
-- Admin stop flags use a separate heavily restricted worker/admin service role;
-- never grant raw access to integration_stop_flags/secret refs to ordinary users.
