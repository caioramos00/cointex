from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('accounts', '0011_alter_notification_options_and_more')]

    operations = [
        migrations.AlterField(
            model_name='pixtransaction',
            name='external_id',
            field=models.CharField(max_length=64, blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='pixtransaction',
            name='transaction_id',
            field=models.CharField(max_length=128, blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='pixtransaction',
            name='hash_id',
            field=models.CharField(max_length=128, blank=True, null=True),
        ),
    ]