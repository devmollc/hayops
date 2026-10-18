import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_cache_table"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MagicLoginToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("next_url", models.CharField(blank=True, max_length=255)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("requested_ip", models.CharField(blank=True, max_length=45)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "source_application",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="core.sourceapplication",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
