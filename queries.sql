-- CDS209 -- NYC Yellow Taxi Geospatial Analytics
-- Student SQL Queries
--
-- Each section corresponds to a task. Write your SQL below the heading.
-- The grader will extract queries by section header.

-- =============================================================================
-- TASK 01a: Create Database
-- =============================================================================
CREATE DATABASE "NYCTaxiDB";

-- =============================================================================
-- TASK 01b: Create TripRecords and TaxiZones tables
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS TripRecords CASCADE;
DROP TABLE IF EXISTS TaxiZones CASCADE;

CREATE TABLE TripRecords (
    trip_id BIGSERIAL PRIMARY KEY,
    vendor_id INTEGER,
    pickup_dt TIMESTAMP,
    dropoff_dt TIMESTAMP,
    passenger_count INTEGER,
    trip_distance DOUBLE PRECISION,
    pu_location_id INTEGER,
    do_location_id INTEGER,
    tip_amount DOUBLE PRECISION,
    total_amount DOUBLE PRECISION
);

CREATE TABLE TaxiZones (
    location_id INTEGER PRIMARY KEY,
    borough TEXT,
    zone TEXT,
    service_zone TEXT
);

CREATE INDEX IF NOT EXISTS idx_triprecords_pickup_dt ON TripRecords (pickup_dt);
CREATE INDEX IF NOT EXISTS idx_triprecords_pu_do_dt ON TripRecords (pu_location_id, do_location_id, pickup_dt);

-- =============================================================================
-- TASK 02a: Add Geometry Columns to TripRecords
-- =============================================================================
ALTER TABLE TripRecords ADD COLUMN IF NOT EXISTS pickup_location geometry(Point, 4326);
ALTER TABLE TripRecords ADD COLUMN IF NOT EXISTS dropoff_location geometry(Point, 4326);

-- =============================================================================
-- TASK 02b: Create Geometry Indexes
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_triprecords_pickup_location ON TripRecords USING GIST (pickup_location);
CREATE INDEX IF NOT EXISTS idx_triprecords_dropoff_location ON TripRecords USING GIST (dropoff_location);
ANALYZE TripRecords;

-- =============================================================================
-- TASK 03: Filter to NYC Bounding Box
-- =============================================================================
DELETE FROM TripRecords
WHERE pickup_location IS NULL
   OR dropoff_location IS NULL
   OR NOT ST_Within(pickup_location, ST_MakeEnvelope(-74.2591, 40.4774, -73.7004, 40.9176, 4326))
   OR NOT ST_Within(dropoff_location, ST_MakeEnvelope(-74.2591, 40.4774, -73.7004, 40.9176, 4326));

-- =============================================================================
-- TASK 04: Invalid Dropoff Timestamps
-- =============================================================================
DELETE FROM TripRecords
WHERE dropoff_dt < pickup_dt;

-- =============================================================================
-- TASK 05: Duplicate/Near-Duplicate Removal
-- =============================================================================
WITH ordered AS (
    SELECT trip_id,
           pickup_dt,
           LAG(pickup_dt) OVER (
               PARTITION BY pu_location_id, do_location_id
               ORDER BY pickup_dt, trip_id
           ) AS prev_pickup_dt
    FROM TripRecords
), duplicates AS (
    SELECT trip_id
    FROM ordered
    WHERE prev_pickup_dt IS NOT NULL
      AND EXTRACT(EPOCH FROM (pickup_dt - prev_pickup_dt)) <= 60
)
DELETE FROM TripRecords tr
USING duplicates d
WHERE tr.trip_id = d.trip_id;

-- =============================================================================
-- TASK 11: Trips and TaxiZones Tables
-- =============================================================================
DROP TABLE IF EXISTS Trips CASCADE;
DROP TABLE IF EXISTS TaxiZonesFull CASCADE;

CREATE TABLE Trips AS
SELECT trip_id,
       vendor_id,
       pickup_dt,
       dropoff_dt,
       pickup_location,
       dropoff_location,
       passenger_count,
       trip_distance,
       EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) / 60.0 AS duration_min,
       (trip_distance * 1.609) / NULLIF(EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) / 3600.0, 0) AS speed_kph,
       tip_amount,
       total_amount,
       pu_location_id,
       do_location_id
FROM TripRecords;

ALTER TABLE Trips ADD PRIMARY KEY (trip_id);
CREATE INDEX IF NOT EXISTS idx_trips_pickup_location ON Trips USING GIST (pickup_location);
CREATE INDEX IF NOT EXISTS idx_trips_dropoff_location ON Trips USING GIST (dropoff_location);
CREATE INDEX IF NOT EXISTS idx_trips_pu_do ON Trips (pu_location_id, do_location_id);

CREATE TABLE TaxiZonesFull (
    location_id INTEGER PRIMARY KEY,
    borough TEXT,
    zone TEXT,
    service_zone TEXT,
    geometry geometry(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_taxizonesfull_geometry ON TaxiZonesFull USING GIST (geometry);
