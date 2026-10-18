DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_app') THEN
        EXECUTE 'CREATE ROLE inbound_app NOLOGIN NOBYPASSRLS';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_app_login') THEN
        EXECUTE 'CREATE ROLE inbound_app_login LOGIN PASSWORD ''inbound_dev_only''';
    END IF;
END
$$;

GRANT inbound_app TO inbound_app_login;
