DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nino_data_readonly') THEN
        CREATE ROLE nino_data_readonly
            LOGIN
            PASSWORD 'nino_data_readonly_password'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE nino_data_demo TO nino_data_readonly;
GRANT USAGE ON SCHEMA nino_data TO nino_data_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA nino_data TO nino_data_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE nino IN SCHEMA nino_data
    GRANT SELECT ON TABLES TO nino_data_readonly;

