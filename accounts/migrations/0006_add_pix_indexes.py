from django.db import migrations

class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('accounts', '0005_customuser_click_type'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_pixtxn_user_created_unpaid_idx
                ON accounts_pixtransaction (user_id, created_at DESC)
                WHERE paid_at IS NULL;
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS accounts_pixtxn_user_created_unpaid_idx;
            """,
        ),
    ]
