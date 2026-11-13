"""Explicit PostgreSQL upgrades; caller owns backup, locks and transaction."""
from sqlalchemy import text

from .db import CURRENT_SCHEMA_VERSION, _connection_schema_state, meta, required_columns
from .migrations import MigrationError, migration_path


LOCK_KEY = 91027602
TABLES = tuple(sorted(table.name for table in meta.sorted_tables))


def tables_for_version(version):
    return tuple(sorted(required_columns(version)))


_LOCK_PROOF = "yeboy_postgres_migration_transaction"


def _transaction(connection):
    if connection.dialect.name != "postgresql":
        raise MigrationError("PostgreSQL maintenance requires PostgreSQL")
    transaction = connection.get_transaction()
    if transaction is None or not connection.in_transaction():
        raise MigrationError("Migration requires an explicit caller-owned transaction")
    return transaction.sync_transaction


async def inspect_postgres(connection):
    """Read validated schema metadata; never bootstrap or alter an existing DB."""
    if connection.dialect.name != "postgresql":
        raise MigrationError("PostgreSQL maintenance requires PostgreSQL")
    if await connection.scalar(text("SELECT current_schema()")) != "public":
        raise MigrationError("PostgreSQL maintenance supports the public schema only")
    return await connection.run_sync(_connection_schema_state)


async def validate_defaults(connection):
    """Reject dependencies omitted by a table-scoped native Core snapshot.

    PostgreSQL dumps an owned SERIAL sequence with its table. An unowned
    sequence, custom collation or RLS policy may instead reference an object or
    global role absent from the archive, even if the table names match. Admit
    only the zero timestamp default and exact owned-serial defaults used by the
    Core metadata; no expression is evaluated as part of this read-only guard.
    """
    version = await inspect_postgres(connection)
    migration_path(version)
    args = {"names": list(tables_for_version(version))}
    table_ids = "SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace AND relname = ANY(:names)"
    if await connection.scalar(text(
            f"SELECT count(*) FROM pg_class WHERE oid IN ({table_ids}) "
            "AND (relrowsecurity OR relforcerowsecurity)"), args):
        raise MigrationError("Row-level security requires a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            f"SELECT count(*) FROM pg_policy WHERE polrelid IN ({table_ids})"), args):
        raise MigrationError("Row policies require a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            "SELECT count(*) FROM pg_attribute a JOIN pg_collation c ON c.oid=a.attcollation "
            f"WHERE a.attrelid IN ({table_ids}) AND a.attnum>0 AND NOT a.attisdropped "
            "AND c.collnamespace<>'pg_catalog'::regnamespace"), args):
        raise MigrationError("Custom collations require a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            f"SELECT count(*) FROM pg_class WHERE oid IN ({table_ids}) "
            "AND relam<>(SELECT oid FROM pg_am WHERE amname='heap')"), args):
        raise MigrationError("Custom table access methods require a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            f"WHERE i.indrelid IN ({table_ids}) AND c.relam<>(SELECT oid FROM pg_am WHERE amname='btree')"), args):
        raise MigrationError("Custom index access methods require a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            "SELECT count(*) FROM pg_index i JOIN LATERAL unnest(i.indclass::oid[]) o(oid) ON true "
            f"JOIN pg_opclass p ON p.oid=o.oid WHERE i.indrelid IN ({table_ids}) "
            "AND p.opcnamespace<>'pg_catalog'::regnamespace"), args):
        raise MigrationError("Custom operator classes require a separate full PostgreSQL backup workflow")
    if await connection.scalar(text(
            "SELECT count(*) FROM pg_index i JOIN LATERAL unnest(i.indcollation::oid[]) o(oid) ON true "
            f"JOIN pg_collation c ON c.oid=o.oid WHERE i.indrelid IN ({table_ids}) "
            "AND c.collnamespace<>'pg_catalog'::regnamespace"), args):
        raise MigrationError("Custom index collations require a separate full PostgreSQL backup workflow")
    defaults = (await connection.execute(text(
        "SELECT c.relname,a.attname,pg_get_expr(d.adbin,d.adrelid) AS expression, "
        "pg_get_serial_sequence(format('public.%I',c.relname),a.attname) IS NOT NULL "
        "AND pg_get_expr(d.adbin,d.adrelid)=format('nextval(%L::regclass)', "
        "pg_get_serial_sequence(format('public.%I',c.relname),a.attname)::regclass::text) AS owned_serial "
        "FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum "
        "JOIN pg_class c ON c.oid=d.adrelid "
        f"WHERE c.oid IN ({table_ids})"), args)).all()
    for table, column, expression, owned_serial in defaults:
        standard_timestamp = version >= 2 and (table, column, expression) == ("v2_cases", "updated", "0")
        if not standard_timestamp and not owned_serial:
            raise MigrationError("Custom defaults require a separate full PostgreSQL backup workflow")


async def lock_source(connection):
    """Exclude concurrent maintenance and writers while allowing pg_dump readers.

    The caller must keep this transaction open across native dump verification
    and migrate_postgres. Startup uses the same advisory lock. A SHARE lock on
    every known table excludes INSERT/UPDATE/DELETE and DDL from other sessions;
    the native dumper may still acquire its ordinary ACCESS SHARE read locks.
    """
    transaction = _transaction(connection)
    connection.info.pop(_LOCK_PROOF, None)
    if not await connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}):
        raise MigrationError("Another PostgreSQL maintenance operation is running")
    current = await inspect_postgres(connection)
    migration_path(current)
    relations = ", ".join(f'public."{name}"' for name in tables_for_version(current))
    await connection.execute(text(f"LOCK TABLE {relations} IN SHARE MODE NOWAIT"))
    if await inspect_postgres(connection) != current:
        raise MigrationError("Schema changed while acquiring PostgreSQL maintenance locks")
    connection.info[_LOCK_PROOF] = transaction
    return current


async def _apply_revision(connection, revision):
    if revision.name == "003_level_rewards" and revision.source == 2 and revision.target == 3:
        await connection.execute(text("CREATE TABLE public.v2_level_rewards (guild_id BIGINT NOT NULL, "
            "kind VARCHAR(5) NOT NULL, level INTEGER NOT NULL, role_id BIGINT NOT NULL, "
            "PRIMARY KEY (guild_id,kind,level))"))
        return
    if revision.name != "002_case_updated_at" or revision.source != 1 or revision.target != 2:
        raise MigrationError("Unknown PostgreSQL migration revision")
    await connection.execute(text(
        "ALTER TABLE public.v2_cases ADD COLUMN updated DOUBLE PRECISION NOT NULL DEFAULT 0"))
    await connection.execute(text("UPDATE public.v2_cases SET updated=created"))


async def migrate_postgres(connection, target=CURRENT_SCHEMA_VERSION):
    """Apply only inside the same transaction that acquired source locks.

    This function never creates backups, commits or rolls back. Callers must
    verify a pre-change native backup before entering this function and must
    roll back on failure. A prior transaction's lock proof is never accepted.
    """
    transaction = _transaction(connection)
    if connection.info.get(_LOCK_PROOF) is not transaction:
        raise MigrationError("Migration requires source locks in the current transaction")
    current = await inspect_postgres(connection)
    pending = migration_path(current, target)
    applied = []
    for revision in pending:
        await _apply_revision(connection, revision)
        result = await connection.execute(text(
            "UPDATE public.v2_schema SET version=:target WHERE version=:source"),
            {"target": revision.target, "source": revision.source})
        if result.rowcount != 1:
            raise MigrationError("Schema marker changed during PostgreSQL migration")
        if await inspect_postgres(connection) != revision.target:
            raise MigrationError("Post-migration PostgreSQL schema validation failed")
        applied.append(revision.name)
    return applied
