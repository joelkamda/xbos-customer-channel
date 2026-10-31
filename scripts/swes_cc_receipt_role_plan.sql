-- DESIGN ONLY: do not run against hosted DB without independently attested isolation.
-- Uses an explicitly synthetic, non-privileged receipt writer. LOGIN credentials
-- must be supplied separately through an approved secret manager.
-- UP / REVIEW ONLY --
CREATE ROLE cc_swes_receipt_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION NOINHERIT;
GRANT CONNECT ON DATABASE xafpay_customer_channel_staging_db TO cc_swes_receipt_test;
GRANT USAGE ON SCHEMA public TO cc_swes_receipt_test;
GRANT SELECT, INSERT, UPDATE ON TABLE public.provider_message_receipt TO cc_swes_receipt_test;
-- Verify no unexpected inherited/PUBLIC table or schema privileges before activation.
-- DOWN / ROLLBACK --
REVOKE SELECT, INSERT, UPDATE ON TABLE public.provider_message_receipt FROM cc_swes_receipt_test;
REVOKE USAGE ON SCHEMA public FROM cc_swes_receipt_test;
REVOKE CONNECT ON DATABASE xafpay_customer_channel_staging_db FROM cc_swes_receipt_test;
DROP ROLE cc_swes_receipt_test;
