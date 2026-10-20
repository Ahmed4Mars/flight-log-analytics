-- =====================================================================
-- Flight-Log Analytics: secondary indexes (MySQL 8)
-- Run after 01_create_tables.sql.
--
-- The time-series tables need nothing here: their composite primary key
-- (flight_id, time_us, ...) already serves "one flight, in time order".
-- An index only helps a query that filters or sorts on its LEFTMOST
-- column(s), so we add indexes only for access paths the PK cannot serve.
-- Try EXPLAIN on a query before and after to see the difference.
-- =====================================================================

USE flight_logs;

-- Event tables have a surrogate id PK, so "events of flight 42 in time
-- order" needs its own index. (MySQL already made an index on flight_id
-- for the foreign key; this one replaces it and adds time order.)
CREATE INDEX idx_events_flight_time   ON events   (flight_id, time_us);
CREATE INDEX idx_errors_flight_time   ON errors   (flight_id, time_us);
CREATE INDEX idx_messages_flight_time ON messages (flight_id, time_us);

-- params PK starts with flight_id, so it cannot help
--   WHERE name = 'ATC_RAT_RLL_P'   (one param across all flights).
-- This index starts with name, and flight_id makes it useful for GROUP BY too.
CREATE INDEX idx_params_name ON params (name, flight_id);

-- "Which flights had failsafe X?" filters on subsys, not flight_id.
CREATE INDEX idx_errors_subsys ON errors (subsys, ecode);

-- "When did each ARMED / DISARMED happen?" filters on event_id.
CREATE INDEX idx_events_event ON events (event_id, flight_id, time_us);
