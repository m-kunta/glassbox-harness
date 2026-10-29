CREATE TABLE IF NOT EXISTS discord_deliveries (
    delivery_id TEXT PRIMARY KEY NOT NULL CHECK (length(delivery_id) = 26 AND substr(delivery_id,1,1) GLOB '[0-7]' AND delivery_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    drift_run_id TEXT NOT NULL REFERENCES drift_runs(drift_run_id) ON DELETE RESTRICT,
    destination_hash TEXT NOT NULL CHECK (length(destination_hash) = 64 AND destination_hash NOT GLOB '*[^0123456789abcdef]*'),
    attempt_number INTEGER NOT NULL CHECK (typeof(attempt_number) = 'integer' AND attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    error_category TEXT CHECK (error_category IS NULL OR error_category IN ('stale_claim', 'transport_error', 'http_429', 'http_5xx', 'http_4xx')),
    created_at TEXT NOT NULL CHECK ((created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,21,3) != '000') OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at,24,3) != '000')) AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(created_at,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(created_at,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(created_at) = substr(created_at,1,10)),
    finalized_at TEXT CHECK (finalized_at IS NULL OR ((finalized_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (finalized_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(finalized_at,21,3) != '000') OR (finalized_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(finalized_at,24,3) != '000')) AND CAST(substr(finalized_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(finalized_at,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(finalized_at,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(finalized_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(finalized_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(finalized_at,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(finalized_at) = substr(finalized_at,1,10))),
    CHECK ((status = 'pending' AND error_category IS NULL AND finalized_at IS NULL)
        OR (status = 'delivered' AND error_category IS NULL AND finalized_at IS NOT NULL)
        OR (status = 'failed' AND error_category IS NOT NULL AND finalized_at IS NOT NULL)),
    -- Pad canonical UTC fractions to compare instants without losing microseconds.
    CHECK (finalized_at IS NULL OR
        (substr(finalized_at,1,19) || substr(rtrim(substr(finalized_at,21),'Z') || '000000',1,6))
        >= (substr(created_at,1,19) || substr(rtrim(substr(created_at,21),'Z') || '000000',1,6))),
    UNIQUE (drift_run_id, destination_hash, attempt_number)
);
CREATE UNIQUE INDEX idx_discord_deliveries_delivered ON discord_deliveries(drift_run_id, destination_hash) WHERE status = 'delivered';
CREATE UNIQUE INDEX idx_discord_deliveries_pending ON discord_deliveries(drift_run_id, destination_hash) WHERE status = 'pending';
CREATE TRIGGER discord_deliveries_no_delete BEFORE DELETE ON discord_deliveries
BEGIN
    SELECT RAISE(ABORT, 'Discord delivery history is immutable');
END;
CREATE TRIGGER discord_deliveries_finalize_only BEFORE UPDATE ON discord_deliveries
WHEN OLD.status != 'pending' OR NEW.status NOT IN ('delivered', 'failed')
    OR NEW.delivery_id IS NOT OLD.delivery_id
    OR NEW.drift_run_id IS NOT OLD.drift_run_id
    OR NEW.destination_hash IS NOT OLD.destination_hash
    OR NEW.attempt_number IS NOT OLD.attempt_number
    OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'Discord delivery history is immutable');
END;
CREATE TRIGGER discord_deliveries_no_replace BEFORE INSERT ON discord_deliveries
WHEN EXISTS (
    SELECT 1 FROM discord_deliveries
    WHERE delivery_id = NEW.delivery_id
       OR (drift_run_id = NEW.drift_run_id AND destination_hash = NEW.destination_hash
           AND (attempt_number = NEW.attempt_number
               OR (status = NEW.status AND status IN ('pending', 'delivered'))))
)
BEGIN
    SELECT RAISE(ABORT, 'Discord delivery history is immutable');
END;
