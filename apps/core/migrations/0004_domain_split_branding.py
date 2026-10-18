from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_add_email_domain"),
    ]

    operations = [
        migrations.RenameField(
            model_name="sourceapplication",
            old_name="email_domain",
            new_name="portal_domain",
        ),
        migrations.AlterField(
            model_name="sourceapplication",
            name="portal_domain",
            field=models.CharField(
                blank=True,
                help_text="Customer portal host for this app, e.g. support.gxpsign.app",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="sourceapplication",
            name="mail_domain",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="SES-verified email domain, e.g. mail.gxpsign.app",
                max_length=255,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="sourceapplication",
            name="product_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Customer-facing name; falls back to name",
                max_length=255,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="sourceapplication",
            name="primary_color",
            field=models.CharField(default="#2563eb", max_length=7),
        ),
        migrations.AddField(
            model_name="sourceapplication",
            name="logo",
            field=models.ImageField(blank=True, upload_to="branding/"),
        ),
        migrations.AddField(
            model_name="sourceapplication",
            name="email_signature",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
