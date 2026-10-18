from django.db import migrations

# Hand-rolled instead of `manage.py createcachetable` so the table can be UNLOGGED:
# no WAL for cache writes, and Postgres truncates it on crash recovery, which is the
# correct behaviour for a cache. Columns must match django.core.cache.backends.db.
CREATE = """
CREATE UNLOGGED TABLE IF NOT EXISTS django_cache (
    cache_key varchar(255) NOT NULL PRIMARY KEY,
    value text NOT NULL,
    expires timestamptz NOT NULL
) WITH (
    -- every cache.set on an existing key is an UPDATE; leave room for HOT updates
    fillfactor = 70,
    -- high-churn table, vacuum it far sooner than the 20% default
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.02
);
CREATE INDEX IF NOT EXISTS django_cache_expires ON django_cache (expires);
"""

DROP = "DROP TABLE IF EXISTS django_cache;"


class Migration(migrations.Migration):
    dependencies = [("core", "0005_alter_sourceapplication_logo")]

    operations = [migrations.RunSQL(CREATE, DROP)]
