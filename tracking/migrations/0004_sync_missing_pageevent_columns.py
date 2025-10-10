from django.db import migrations

SQL = """
-- Mantém idempotente: IF NOT EXISTS já cobre repetição.
ALTER TABLE public.tracking_pageeventconfig
  ADD COLUMN IF NOT EXISTS fire_search boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS search_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_add_to_cart boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS add_to_cart_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_add_to_wishlist boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS add_to_wishlist_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_add_payment_info boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS add_payment_info_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_lead boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS lead_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_complete_registration boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS complete_registration_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_subscribe boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS subscribe_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_start_trial boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS start_trial_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_contact boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS contact_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_find_location boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS find_location_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_schedule boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS schedule_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_submit_application boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS submit_application_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_customize_product boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS customize_product_params text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS fire_donate boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS donate_params text NOT NULL DEFAULT '';
"""

class Migration(migrations.Migration):

    dependencies = [
        ('tracking', '0003_pageeventconfig_and_more'),
    ]

    operations = [
        migrations.RunSQL(SQL, reverse_sql=migrations.RunSQL.noop),
    ]
