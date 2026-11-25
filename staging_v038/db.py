"""Database context for the synthetic staging harness.

Only trusted server-side identity may supply these UUIDs. The custom settings
protect against omitted query filters, not arbitrary SQL as the runtime role.
"""
from contextlib import contextmanager
from uuid import UUID

from psycopg_pool import ConnectionPool


def reset_connection(connection):
    """Clear session state before the pool lends this backend again."""
    connection.autocommit = True
    try:
        connection.execute("DISCARD ALL")
    finally:
        connection.autocommit = False


def open_pool(**connection_parameters):
    return ConnectionPool(
        kwargs={**connection_parameters, "prepare_threshold": None},
        min_size=1,
        max_size=1,
        timeout=10,
        reset=reset_connection,
        open=False,
    )


@contextmanager
def tenant_transaction(pool, *, tenant: UUID, user: UUID):
    if not isinstance(tenant, UUID) or not isinstance(user, UUID):
        raise TypeError("tenant and user must be UUIDs from trusted identity")
    with pool.connection() as connection, connection.transaction():
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.user_id', %s, true)",
            (str(tenant), str(user)),
        )
        yield connection
