-- =====================================================================
-- Flight-Log Analytics: tables (MySQL 8)
--
-- Run in MySQL Workbench (lightning-bolt icon), then 02_indexes.sql.
-- WARNING: re-running drops every table, so all loaded data is lost.
--          Reload afterwards with src/load_mysql.py.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS flight_logs
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE flight_logs;

-- Children first: a parent table cannot be dropped while a foreign key points at it
DROP TABLE IF EXISTS gps, battery, attitude, altitude, vibration, params,
                     modes, events, errors, messages;
DROP TABLE IF EXISTS flights, mode_names, event_names, error_subsystems;


-- ---------------------------------------------------------------------
-- Lookup tables: turn numeric codes into readable names via JOIN
-- ---------------------------------------------------------------------

-- ArduCopter flight modes (from pymavlink mode_mapping_acm)
CREATE TABLE mode_names (
  mode_num  TINYINT UNSIGNED PRIMARY KEY,
  name      VARCHAR(20) NOT NULL UNIQUE
);

-- EV message ids (ArduPilot AP_Logger LogEvent enum).
-- No foreign key points here on purpose: an id missing from this list
-- must not block a load. Find unknown ids with a LEFT JOIN instead.
CREATE TABLE event_names (
  event_id  TINYINT UNSIGNED PRIMARY KEY,
  name      VARCHAR(40) NOT NULL
);

-- ERR message subsystems (ArduPilot AP_Logger LogErrorSubsystem enum).
-- ecode meaning depends on the subsystem; for failsafes 0 = cleared, 1 = triggered.
CREATE TABLE error_subsystems (
  subsys    TINYINT UNSIGNED PRIMARY KEY,
  name      VARCHAR(40) NOT NULL
);


-- ---------------------------------------------------------------------
-- flights: one row per .BIN log file
-- ---------------------------------------------------------------------
CREATE TABLE flights (
  flight_id  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  log_file   VARCHAR(20)  NOT NULL,             -- e.g. '00000089.BIN' (numbers repeat after an SD wipe)
  file_sha1  CHAR(40)     NOT NULL UNIQUE,      -- same file = same hash: makes reloads idempotent
  file_mb    DECIMAL(6,1) NOT NULL,
  firmware   VARCHAR(60)  DEFAULT NULL,         -- e.g. 'ArduCopter V4.7.1 (dbe79216)'
  start_utc  DATETIME     DEFAULT NULL,         -- UTC at boot (time_us = 0), from first 3D GPS fix; NULL if none
  loaded_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ---------------------------------------------------------------------
-- Time series (10 Hz, GPS 5 Hz)
-- Composite PK (flight_id, time_us[, instance]): unique in all 103 logs.
-- InnoDB stores rows sorted by the PK, so one flight's rows in time
-- order sit together on disk.
-- time_us is BIGINT: INT UNSIGNED overflows after 71.6 minutes of µs.
-- ---------------------------------------------------------------------

-- GPS
CREATE TABLE gps (
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,        -- microseconds since boot
  instance    TINYINT UNSIGNED NOT NULL,        -- GPS receiver (0, 1)
  status      TINYINT UNSIGNED NOT NULL,        -- 0 no GPS, 1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK float, 6 RTK fixed
  num_sats    TINYINT UNSIGNED,
  hdop        FLOAT,
  lat         DECIMAL(10,7),                    -- degrees, exact to ~1 cm
  lng         DECIMAL(10,7),
  alt_m       FLOAT,                            -- altitude above mean sea level (m)
  speed_ms    FLOAT,                            -- ground speed (m/s)
  course_deg  FLOAT,                            -- ground course (deg, 0 = north)
  vz_ms       FLOAT,                            -- vertical speed (m/s, positive = down)
  PRIMARY KEY (flight_id, time_us, instance),
  CONSTRAINT fk_gps_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- BAT
CREATE TABLE battery (
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,
  instance    TINYINT UNSIGNED NOT NULL,        -- battery monitor
  volt        FLOAT,                            -- V
  volt_rest   FLOAT,                            -- estimated resting voltage, sag removed (V)
  curr_a      FLOAT,                            -- A
  used_mah    FLOAT,                            -- consumed since boot (mAh)
  used_wh     FLOAT,                            -- consumed since boot (Wh)
  temp_c      FLOAT,
  rem_pct     TINYINT UNSIGNED,                 -- remaining capacity (%)
  PRIMARY KEY (flight_id, time_us, instance),
  CONSTRAINT fk_battery_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- ATT: desired vs actual attitude (deg)
CREATE TABLE attitude (
  flight_id   INT UNSIGNED    NOT NULL,
  time_us     BIGINT UNSIGNED NOT NULL,
  des_roll    FLOAT,
  roll        FLOAT,
  des_pitch   FLOAT,
  pitch       FLOAT,
  des_yaw     FLOAT,                            -- 0..360
  yaw         FLOAT,
  PRIMARY KEY (flight_id, time_us),
  CONSTRAINT fk_attitude_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- CTUN: altitude and throttle control
CREATE TABLE altitude (
  flight_id       INT UNSIGNED    NOT NULL,
  time_us         BIGINT UNSIGNED NOT NULL,
  thr_in          FLOAT,                        -- pilot throttle input (0..1)
  thr_out         FLOAT,                        -- throttle sent to motors (0..1)
  thr_hover       FLOAT,                        -- learned hover throttle (0..1)
  des_alt         FLOAT,                        -- desired altitude above home (m)
  alt             FLOAT,                        -- EKF altitude above home (m)
  baro_alt        FLOAT,                        -- barometer altitude (m)
  rf_alt          FLOAT,                        -- rangefinder altitude (m)
  des_climb_rate  FLOAT,                        -- m/s
  climb_rate      FLOAT,                        -- m/s, positive = up
  PRIMARY KEY (flight_id, time_us),
  CONSTRAINT fk_altitude_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- VIBE: one row per IMU per sample
CREATE TABLE vibration (
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,
  imu         TINYINT UNSIGNED NOT NULL,        -- 0, 1, 2
  vibe_x      FLOAT,                            -- m/s/s
  vibe_y      FLOAT,
  vibe_z      FLOAT,
  clip        INT UNSIGNED,                     -- accelerometer clipping count since boot (cumulative)
  PRIMARY KEY (flight_id, time_us, imu),
  CONSTRAINT fk_vibration_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- PARM: a parameter is logged at boot and again whenever it changes,
-- so one flight can hold several rows for the same name.
CREATE TABLE params (
  flight_id      INT UNSIGNED    NOT NULL,
  name           VARCHAR(16)     NOT NULL,      -- ArduPilot param names are max 16 chars
  time_us        BIGINT UNSIGNED NOT NULL,
  value          FLOAT           NOT NULL,
  default_value  FLOAT,                         -- 'default' is a reserved word in SQL
  PRIMARY KEY (flight_id, name, time_us),
  CONSTRAINT fk_params_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);


-- ---------------------------------------------------------------------
-- Discrete events
-- ---------------------------------------------------------------------

-- MODE: one row per mode change
CREATE TABLE modes (
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,
  mode_num    TINYINT UNSIGNED NOT NULL,
  reason      TINYINT UNSIGNED,                 -- why the mode changed (RC switch, GCS, failsafe, ...)
  PRIMARY KEY (flight_id, time_us),
  CONSTRAINT fk_modes_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE,
  CONSTRAINT fk_modes_mode FOREIGN KEY (mode_num)
    REFERENCES mode_names (mode_num)
);

-- The next three use a surrogate id: nothing guarantees that two events
-- never share a microsecond. Their (flight_id, time_us) index is in 02_indexes.sql.

-- EV: arm, disarm, land complete, EKF resets, ...
CREATE TABLE events (
  id          BIGINT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,
  event_id    TINYINT UNSIGNED NOT NULL,        -- see event_names (no FK, on purpose)
  CONSTRAINT fk_events_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- ERR: failsafes and subsystem errors
CREATE TABLE errors (
  id          BIGINT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
  flight_id   INT UNSIGNED     NOT NULL,
  time_us     BIGINT UNSIGNED  NOT NULL,
  subsys      TINYINT UNSIGNED NOT NULL,        -- see error_subsystems (no FK, on purpose)
  ecode       TINYINT UNSIGNED NOT NULL,
  CONSTRAINT fk_errors_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);

-- MSG: text messages (prearm failures, mission items, EKF notices, ...)
CREATE TABLE messages (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  flight_id   INT UNSIGNED    NOT NULL,
  time_us     BIGINT UNSIGNED NOT NULL,
  message     VARCHAR(255)    NOT NULL,
  CONSTRAINT fk_messages_flight FOREIGN KEY (flight_id)
    REFERENCES flights (flight_id) ON DELETE CASCADE
);


-- ---------------------------------------------------------------------
-- Lookup data
-- ---------------------------------------------------------------------

INSERT INTO mode_names (mode_num, name) VALUES
  (0,'STABILIZE'), (1,'ACRO'), (2,'ALT_HOLD'), (3,'AUTO'), (4,'GUIDED'),
  (5,'LOITER'), (6,'RTL'), (7,'CIRCLE'), (8,'POSITION'), (9,'LAND'),
  (10,'OF_LOITER'), (11,'DRIFT'), (13,'SPORT'), (14,'FLIP'), (15,'AUTOTUNE'),
  (16,'POSHOLD'), (17,'BRAKE'), (18,'THROW'), (19,'AVOID_ADSB'),
  (20,'GUIDED_NOGPS'), (21,'SMART_RTL'), (22,'FLOWHOLD'), (23,'FOLLOW'),
  (24,'ZIGZAG'), (25,'SYSTEMID'), (26,'AUTOROTATE'), (27,'AUTO_RTL'),
  (28,'TURTLE'), (29,'RATE_ACRO');

INSERT INTO event_names (event_id, name) VALUES
  (10,'ARMED'), (11,'DISARMED'), (15,'AUTO_ARMED'),
  (17,'LAND_COMPLETE_MAYBE'), (18,'LAND_COMPLETE'), (19,'LOST_GPS'),
  (21,'FLIP_START'), (22,'FLIP_END'), (25,'SET_HOME'),
  (26,'SET_SIMPLE_ON'), (27,'SET_SIMPLE_OFF'), (28,'NOT_LANDED'),
  (29,'SET_SUPERSIMPLE_ON'), (30,'AUTOTUNE_INITIALISED'), (31,'AUTOTUNE_OFF'),
  (32,'AUTOTUNE_RESTART'), (33,'AUTOTUNE_SUCCESS'), (34,'AUTOTUNE_FAILED'),
  (35,'AUTOTUNE_REACHED_LIMIT'), (36,'AUTOTUNE_PILOT_TESTING'),
  (37,'AUTOTUNE_SAVEDGAINS'), (38,'SAVE_TRIM'), (39,'SAVEWP_ADD_WP'),
  (41,'FENCE_ENABLE'), (42,'FENCE_DISABLE'),
  (43,'ACRO_TRAINER_OFF'), (44,'ACRO_TRAINER_LEVELING'), (45,'ACRO_TRAINER_LIMITED'),
  (46,'GRIPPER_GRAB'), (47,'GRIPPER_RELEASE'),
  (49,'PARACHUTE_DISABLED'), (50,'PARACHUTE_ENABLED'), (51,'PARACHUTE_RELEASED'),
  (52,'LANDING_GEAR_DEPLOYED'), (53,'LANDING_GEAR_RETRACTED'),
  (54,'MOTORS_EMERGENCY_STOPPED'), (55,'MOTORS_EMERGENCY_STOP_CLEARED'),
  (56,'MOTORS_INTERLOCK_DISABLED'), (57,'MOTORS_INTERLOCK_ENABLED'),
  (58,'ROTOR_RUNUP_COMPLETE'), (59,'ROTOR_SPEED_BELOW_CRITICAL'),
  (60,'EKF_ALT_RESET'), (61,'LAND_CANCELLED_BY_PILOT'), (62,'EKF_YAW_RESET'),
  (63,'AVOIDANCE_ADSB_ENABLE'), (64,'AVOIDANCE_ADSB_DISABLE'),
  (65,'AVOIDANCE_PROXIMITY_ENABLE'), (66,'AVOIDANCE_PROXIMITY_DISABLE'),
  (67,'GPS_PRIMARY_CHANGED');

INSERT INTO error_subsystems (subsys, name) VALUES
  (1,'MAIN'), (2,'RADIO'), (3,'COMPASS'), (4,'OPTFLOW'),
  (5,'FAILSAFE_RADIO'), (6,'FAILSAFE_BATT'), (7,'FAILSAFE_GPS'),
  (8,'FAILSAFE_GCS'), (9,'FAILSAFE_FENCE'), (10,'FLIGHT_MODE'), (11,'GPS'),
  (12,'CRASH_CHECK'), (13,'FLIP'), (14,'AUTOTUNE'), (15,'PARACHUTES'),
  (16,'EKFCHECK'), (17,'FAILSAFE_EKFINAV'), (18,'BARO'), (19,'CPU'),
  (20,'FAILSAFE_ADSB'), (21,'TERRAIN'), (22,'NAVIGATION'),
  (23,'FAILSAFE_TERRAIN'), (24,'EKF_PRIMARY'), (25,'THRUST_LOSS_CHECK'),
  (26,'FAILSAFE_SENSORS'), (27,'FAILSAFE_LEAK'), (28,'PILOT_INPUT'),
  (29,'FAILSAFE_VIBE'), (30,'INTERNAL_ERROR'), (31,'FAILSAFE_DEADRECKON');
