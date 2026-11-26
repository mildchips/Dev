"""Explicit opt-in tests against a fresh PostgreSQL container and real logins."""
import os
import secrets
import subprocess
import time
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import errors, sql

from staging_v038.db import open_pool, tenant_transaction

IMAGE = "postgres:17.7-bookworm@sha256:86e0b703649d7a792bd9243ee28afc9d8f7c6b2b5638077c9d6882d4d472bbfd"
MIGRATION = Path(__file__).resolve().parents[1] / "deployment/postgres/002_v038_synthetic.sql"
TENANT_A, TENANT_B, STORE_A, STORE_B, OWNER, MANAGER, AUDITOR, EXPIRED, REVOKED = (
    UUID(int=index) for index in range(1, 10)
)


def docker(*args, **kwargs):
    return subprocess.run(
        ("docker", *args), check=True, text=True, capture_output=True, **kwargs
    ).stdout.strip()


@pytest.fixture(scope="module")
def postgres():
    if os.environ.get("AIPO_RUN_PG_STAGING") != "1":
        pytest.skip("set AIPO_RUN_PG_STAGING=1 to start the disposable synthetic cluster")
    password = secrets.token_urlsafe(32)
    container = docker(
        "run", "--rm", "-d", "--label", "aipo.scope=synthetic-v038",
        "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD",
        IMAGE, env={**os.environ, "POSTGRES_PASSWORD": password},
    )
    try:
        host, port = docker("port", container, "5432/tcp").split(":")
        assert host == "127.0.0.1"
        base = dict(host=host, port=port, connect_timeout=2)
        for attempt in range(40):
            try:
                admin = psycopg.connect(**base, user="postgres", password=password, autocommit=True)
                break
            except psycopg.OperationalError:
                if attempt == 39:
                    raise
                time.sleep(0.25)
        roles = {}
        with admin:
            assert admin.info.server_version == 170007
            for name in ("migration", "api", "worker", "backup"):
                username = f"aipo_{name}"
                secret = secrets.token_urlsafe(32)
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD {}").format(
                        sql.Identifier(username), sql.Literal(secret)
                    )
                )
                roles[name] = dict(**base, dbname="aipo_staging", user=username, password=secret)
            admin.execute("CREATE DATABASE aipo_staging OWNER aipo_migration")
            admin.execute("REVOKE ALL ON DATABASE aipo_staging FROM PUBLIC")
            admin.execute("GRANT CONNECT ON DATABASE aipo_staging TO aipo_api,aipo_worker,aipo_backup")
            with psycopg.connect(**base, dbname="aipo_staging", user="postgres", password=password) as seed:
                seed.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                with psycopg.connect(**roles["migration"], autocommit=True) as migration:
                    migration.execute(MIGRATION.read_text())
                for tenant, store in ((TENANT_A, STORE_A), (TENANT_A, STORE_B), (TENANT_B, STORE_A), (TENANT_B, STORE_B)):
                    seed.execute("INSERT INTO aipo_data.stores VALUES (%s,%s,'synthetic')", (tenant, store))
                    seed.execute(
                        "INSERT INTO aipo_data.reports VALUES (%s,%s,%s,'fixture','{}')", (tenant, store, store)
                    )
                for user, role, active, expires in (
                    (OWNER, "owner", True, "1 day"),
                    (MANAGER, "manager", True, "1 day"),
                    (AUDITOR, "auditor", True, "1 day"),
                    (EXPIRED, "manager", True, "-1 day"),
                    (REVOKED, "manager", False, "1 day"),
                ):
                    seed.execute(
                        "INSERT INTO aipo_auth.membership VALUES "
                        "(%s,%s,%s,%s,%s,now()+%s::interval)",
                        (TENANT_A, user, role, [STORE_A], active, expires),
                    )
                seed.execute(
                    "INSERT INTO aipo_auth.membership VALUES (%s,%s,'owner','{}',true,now()+interval '1 day')",
                    (TENANT_B, OWNER),
                )
        yield container, roles
    finally:
        docker("stop", "-t", "2", container)


@pytest.fixture
def roles(postgres):
    return postgres[1]


def stores(connection):
    return set(connection.execute("SELECT tenant,id FROM aipo_data.stores").fetchall())


@pytest.mark.parametrize("role", ["api", "worker"])
def test_tenant_store_visibility(roles, role):
    with open_pool(**roles[role]) as pool:
        for user, expected in (
            (OWNER, {(TENANT_A, STORE_A), (TENANT_A, STORE_B)}),
            (MANAGER, {(TENANT_A, STORE_A)}),
            (AUDITOR, {(TENANT_A, STORE_A)}),
            (EXPIRED, set()),
            (REVOKED, set()),
        ):
            with tenant_transaction(pool, tenant=TENANT_A, user=user) as connection:
                assert stores(connection) == expected
                assert set(connection.execute("SELECT tenant,store FROM aipo_data.reports")) == expected
        with tenant_transaction(pool, tenant=TENANT_B, user=MANAGER) as connection:
            assert stores(connection) == set()


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("abort", [False, True])
def test_pool_reuse_after_commit_and_rollback(roles, role, abort):
    with open_pool(**roles[role]) as pool:
        with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
            backend = connection.info.backend_pid
            assert len(stores(connection)) == 2
        with pool.connection() as connection:
            assert connection.info.backend_pid == backend
            assert stores(connection) == set()
            # Deliberately dirty the session to verify reset independently of SET LOCAL.
            connection.execute("SELECT set_config('app.user_id', %s, false)", (str(OWNER),))
            connection.execute("SELECT set_config('app.tenant_id', %s, false)", (str(TENANT_A),))
        if abort:
            with pytest.raises(errors.DivisionByZero):
                with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
                    connection.execute("SELECT 1/0")
        with pool.connection() as connection:
            assert connection.info.backend_pid == backend
            assert stores(connection) == set()
        with tenant_transaction(pool, tenant=TENANT_B, user=OWNER) as connection:
            assert connection.info.backend_pid == backend
            assert stores(connection) == {(TENANT_B, STORE_A), (TENANT_B, STORE_B)}


@pytest.mark.parametrize("role,statement", [
    ("api", "SELECT * FROM aipo_auth.membership"),
    ("worker", "UPDATE aipo_data.stores SET label='changed'"),
    ("api", "INSERT INTO aipo_data.reports SELECT * FROM aipo_data.reports"),
    ("worker", "DELETE FROM aipo_data.reports"),
    ("backup", "SELECT * FROM aipo_auth.membership"),
    ("backup", "DELETE FROM aipo_data.stores"),
    ("api", "SET ROLE aipo_migration"),
    ("worker", "SET ROLE aipo_backup"),
    ("api", "CREATE TABLE public.forbidden (id int)"),
])
def test_role_privileges(roles, role, statement):
    with psycopg.connect(**roles[role]) as connection:
        with pytest.raises(errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute(statement)


def test_backup_and_restore_synthetic_data(postgres, tmp_path):
    container, roles = postgres
    backup = roles["backup"]
    dump = docker(
        "exec", "-e", "PGPASSWORD", container,
        "pg_dump", "-h", "127.0.0.1", "-U", backup["user"], "-d", backup["dbname"],
        "--enable-row-security", "--data-only", "--inserts",
        "--table=aipo_data.stores", "--table=aipo_data.reports",
        env={**os.environ, "PGPASSWORD": backup["password"]},
    )
    snapshot = tmp_path / "synthetic.sql"
    snapshot.write_text(dump)
    docker("exec", container, "createdb", "-U", "postgres", "-O", "aipo_migration", "aipo_restored")
    docker(
        "exec", container, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres",
        "-d", "aipo_restored", "-c",
        "REVOKE ALL ON DATABASE aipo_restored FROM PUBLIC; "
        "REVOKE ALL ON SCHEMA public FROM PUBLIC; "
        "GRANT CONNECT ON DATABASE aipo_restored TO aipo_backup",
    )
    with psycopg.connect(**{**roles["migration"], "dbname": "aipo_restored"}, autocommit=True) as connection:
        connection.execute(MIGRATION.read_text())
    docker(
        "exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "--single-transaction", "-U", "postgres", "-d", "aipo_restored",
        input=snapshot.read_text(),
    )
    with psycopg.connect(**backup) as original, psycopg.connect(
        **{**backup, "dbname": "aipo_restored"}
    ) as restored:
        for name in ("stores", "reports"):
            query = sql.SQL("SELECT * FROM aipo_data.{} ORDER BY tenant,id").format(sql.Identifier(name))
            expected = original.execute(query).fetchall()
            assert len(expected) == 4
            assert restored.execute(query).fetchall() == expected
    with psycopg.connect(**{**roles["migration"], "dbname": "aipo_restored"}) as migration:
        assert migration.execute("SELECT count(*) FROM aipo_auth.membership").fetchone() == (0,)


class RollbackFixture(Exception):
    """Keep the staging seed independent of the test execution order."""


@pytest.mark.parametrize("tenant,store,user,allowed", [
    (TENANT_A, STORE_A, MANAGER, True),
    (TENANT_A, STORE_B, MANAGER, False),
    (TENANT_A, STORE_A, AUDITOR, False),
    (TENANT_A, STORE_A, EXPIRED, False),
    (TENANT_A, STORE_A, REVOKED, False),
    (TENANT_B, STORE_A, OWNER, False),
])
def test_worker_report_write_scope(roles, tenant, store, user, allowed):
    # Dispatch always occurs under tenant A, independently of payload fields.
    with open_pool(**roles["worker"]) as pool:
        def write():
            with tenant_transaction(pool, tenant=TENANT_A, user=user) as connection:
                connection.execute(
                    "INSERT INTO aipo_data.reports VALUES (%s,%s,%s,'new-fixture','{}')",
                    (tenant, UUID(int=100), store),
                )
                raise RollbackFixture

        if allowed:
            with pytest.raises(RollbackFixture):
                write()
        else:
            with pytest.raises(errors.InsufficientPrivilege):
                write()


def test_api_store_update_is_owner_only_and_cannot_move_store(roles):
    with open_pool(**roles["api"]) as pool:
        for user, expected in ((MANAGER, 0), (AUDITOR, 0), (OWNER, 2)):
            with pytest.raises(RollbackFixture):
                with tenant_transaction(pool, tenant=TENANT_A, user=user) as connection:
                    assert connection.execute(
                        "UPDATE aipo_data.stores SET label='renamed'"
                    ).rowcount == expected
                    raise RollbackFixture
        with pytest.raises(errors.InsufficientPrivilege):
            with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
                connection.execute("UPDATE aipo_data.stores SET tenant=%s", (TENANT_B,))


def test_committed_revocation_is_visible_on_same_pooled_backend(roles):
    with open_pool(**roles["worker"]) as pool:
        with tenant_transaction(pool, tenant=TENANT_A, user=MANAGER) as connection:
            backend = connection.info.backend_pid
            assert stores(connection) == {(TENANT_A, STORE_A)}
            with psycopg.connect(**roles["migration"]) as migration:
                migration.execute(
                    "UPDATE aipo_auth.membership SET active=false WHERE tenant=%s AND user_id=%s",
                    (TENANT_A, MANAGER),
                )
            try:
                assert connection.info.backend_pid == backend
                assert stores(connection) == set()
            finally:
                with psycopg.connect(**roles["migration"]) as migration:
                    migration.execute(
                        "UPDATE aipo_auth.membership SET active=true WHERE tenant=%s AND user_id=%s",
                        (TENANT_A, MANAGER),
                    )


@pytest.mark.parametrize("role", ["api", "worker", "backup"])
def test_runtime_roles_cannot_bypass_rls(roles, role):
    with psycopg.connect(**roles[role]) as connection:
        assert connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False, False, False, False)
        assert connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) "
            "FROM pg_class WHERE oid IN ('aipo_data.stores'::regclass,'aipo_data.reports'::regclass)"
        ).fetchall() == [(True, True, "aipo_migration")] * 2
        with pytest.raises(errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute("SET LOCAL row_security=off")
                connection.execute("SELECT * FROM aipo_data.stores")


def test_api_cannot_create_update_or_delete_other_tenants_store(roles):
    with open_pool(**roles["api"]) as pool:
        with pytest.raises(errors.InsufficientPrivilege):
            with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
                connection.execute(
                    "INSERT INTO aipo_data.stores VALUES (%s,%s,'blocked')",
                    (TENANT_B, UUID(int=101)),
                )
        with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
            assert connection.execute(
                "UPDATE aipo_data.stores SET label='blocked' WHERE tenant=%s", (TENANT_B,)
            ).rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
                connection.execute("DELETE FROM aipo_data.stores WHERE tenant=%s", (TENANT_B,))


def test_worker_report_fk_requires_matching_tenant_and_store(postgres):
    container, roles = postgres
    isolated_store = UUID(int=102)
    with psycopg.connect(**roles["api"]) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true)",
                (str(TENANT_B), str(OWNER)),
            )
            connection.execute(
                "INSERT INTO aipo_data.stores VALUES (%s,%s,'tenant-B-only')",
                (TENANT_B, isolated_store),
            )
    try:
        with open_pool(**roles["worker"]) as pool:
            with pytest.raises(errors.ForeignKeyViolation):
                with tenant_transaction(pool, tenant=TENANT_A, user=OWNER) as connection:
                    connection.execute(
                        "INSERT INTO aipo_data.reports VALUES (%s,%s,%s,'blocked','{}')",
                        (TENANT_A, UUID(int=103), isolated_store),
                    )
    finally:
        docker(
            "exec", container, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres",
            "-d", "aipo_staging", "-c",
            f"DELETE FROM aipo_data.stores WHERE tenant='{TENANT_B}' AND id='{isolated_store}'",
        )
