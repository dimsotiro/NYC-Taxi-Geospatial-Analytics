"""

CDS209 -- NYC Yellow Taxi Geospatial Analytics
Student Submission Skeleton
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from psycopg2.extras import execute_values
from shapely.geometry import box

from connection import close_connection, execute_sql, execute_sql_batch, get_connection, get_sql

DATA_DIR: Path = Path(__file__).parent / "data"

# Time window for analytical queries (tasks 7-10)
WINDOW_START = "2026-01-15 12:00:00"
WINDOW_END = "2026-01-15 14:00:00"

# Bounding box
MIN_LAT, MIN_LON = 40.4774, -74.2591
MAX_LAT, MAX_LON = 40.9176, -73.7004

# Speed conversion constants
MILES_TO_KM = 1.609
MAX_SPEED_KPH = 120.0

# Optional output slots required by assignment
task_06_explanation = ""
task_08_pairs: list[tuple[int, int]] = []
task_10_grid_gdf: gpd.GeoDataFrame | None = None
task_14_model: Any = None


# --------------------------------------------------------------------------- #
# Task 1: Create TripRecords Table and Populate (10%)
# --------------------------------------------------------------------------- #


def task_01() -> int:
    """Create database/tables and load trip CSV data into TripRecords."""
    admin_conn = get_connection(dbname="postgres")
    admin_conn.autocommit = True
    admin_cur = admin_conn.cursor()

    sql = get_sql(1, "a")
    execute_sql_batch(sql, admin_conn)
    admin_cur.execute("SELECT 1 FROM pg_database WHERE datname = 'NYCTaxiDB'")
    if not admin_cur.fetchone():
        raise RuntimeError("Database NYCTaxiDB was not created successfully.")
    close_connection(admin_conn)

    sql = get_sql(1, "b")
    conn = get_connection()
    try:
        execute_sql_batch(sql, conn)

        # Load taxi zone lookup
        zones_path = DATA_DIR / "taxi_zone_lookup.csv"
        zones = pd.read_csv(zones_path).rename(columns={
            "LocationID": "location_id", "Borough": "borough", "Zone": "zone"
        })
        zone_rows = list(zones[["location_id", "borough", "zone", "service_zone"]].itertuples(index=False, name=None))
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE TaxiZones;")
            execute_values(cur, """
                INSERT INTO TaxiZones (location_id, borough, zone, service_zone)
                VALUES %s
                ON CONFLICT (location_id) DO UPDATE SET
                    borough = EXCLUDED.borough,
                    zone = EXCLUDED.zone,
                    service_zone = EXCLUDED.service_zone
            """, zone_rows, page_size=1000)
        conn.commit()

        # We converted parquet to data.csv using duckDB.
        csv_path = DATA_DIR / "data.csv"
        df = pd.read_csv(csv_path)
        df = df.rename(columns={
            "VendorID": "vendor_id",
            "tpep_pickup_datetime": "pickup_dt",
            "tpep_dropoff_datetime": "dropoff_dt",
            "PULocationID": "pu_location_id",
            "DOLocationID": "do_location_id",
        })
        df = df[[
            "vendor_id", "pickup_dt", "dropoff_dt", "passenger_count", "trip_distance",
            "pu_location_id", "do_location_id", "tip_amount", "total_amount"
        ]]
        df["pickup_dt"] = pd.to_datetime(df["pickup_dt"], errors="coerce")
        df["dropoff_dt"] = pd.to_datetime(df["dropoff_dt"], errors="coerce")
        df = df.where(pd.notna(df), None)

        def py_value(v: Any) -> Any:
            if pd.isna(v):
                return None
            if hasattr(v, "to_pydatetime"):
                return v.to_pydatetime()
            if hasattr(v, "item"):
                return v.item()
            return v

        rows = [tuple(py_value(v) for v in row) for row in df.itertuples(index=False, name=None)]
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE TripRecords RESTART IDENTITY;")
            execute_values(cur, """
                INSERT INTO TripRecords
                (vendor_id, pickup_dt, dropoff_dt, passenger_count, trip_distance,
                 pu_location_id, do_location_id, tip_amount, total_amount)
                VALUES %s
            """, rows, page_size=10000)
        conn.commit()
        return len(rows)
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 2: Populate Geometry Columns (5%)
# --------------------------------------------------------------------------- #


def task_02() -> int:
    """Populate pickup/dropoff point geometries from taxi-zone centroids."""
    conn = get_connection()
    try:
        execute_sql_batch(get_sql(2, "a"), conn)

        # Find or extract shapefile directly in this task to keep the skeleton simple.
        shp_files = list(DATA_DIR.glob("**/taxi_zones.shp")) + list(DATA_DIR.glob("**/*.shp"))
        if not shp_files:
            zip_path = DATA_DIR / "taxi_zones.zip"
            extract_dir = DATA_DIR / "taxi_zones"
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
            shp_files = list(extract_dir.glob("**/taxi_zones.shp")) + list(extract_dir.glob("**/*.shp"))
        shp = shp_files[0]

        zones_gdf = gpd.read_file(shp).to_crs(epsg=4326)
        id_col = "LocationID" if "LocationID" in zones_gdf.columns else "location_id"
        centroids = zones_gdf.to_crs(epsg=2263).centroid
        centroids = gpd.GeoSeries(centroids, crs=2263).to_crs(epsg=4326)
        rows = [(int(loc), float(pt.x), float(pt.y)) for loc, pt in zip(zones_gdf[id_col], centroids)]

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS zone_centroids_tmp;")
            cur.execute("CREATE TEMP TABLE zone_centroids_tmp (location_id integer PRIMARY KEY, lon double precision, lat double precision);")
            execute_values(cur, "INSERT INTO zone_centroids_tmp (location_id, lon, lat) VALUES %s", rows, page_size=1000)
            cur.execute("""
                UPDATE TripRecords tr
                SET pickup_location = ST_SetSRID(ST_MakePoint(pu.lon, pu.lat), 4326),
                    dropoff_location = ST_SetSRID(ST_MakePoint(doz.lon, doz.lat), 4326)
                FROM zone_centroids_tmp pu, zone_centroids_tmp doz
                WHERE tr.pu_location_id = pu.location_id
                  AND tr.do_location_id = doz.location_id;
            """)
        conn.commit()

        execute_sql_batch(get_sql(2, "b"), conn)
        return execute_sql("SELECT COUNT(*) FROM TripRecords WHERE pickup_location IS NOT NULL AND dropoff_location IS NOT NULL", conn)[0][0]
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 3: Filter to NYC Bounding Box (5%)
# --------------------------------------------------------------------------- #


def task_03() -> tuple[int, int]:
    """DELETE records outside NYC bounding box and return count before/after."""
    conn = get_connection()
    try:
        before = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        execute_sql_batch(get_sql(3), conn)
        after = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        return before, after
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 4: Invalid Dropoff Timestamps (5%)
# --------------------------------------------------------------------------- #


def task_04() -> int:
    """Remove records where dropoff precedes pickup."""
    conn = get_connection()
    try:
        removed = execute_sql("SELECT COUNT(*) FROM TripRecords WHERE dropoff_dt < pickup_dt", conn)[0][0]
        execute_sql_batch(get_sql(4), conn)
        return removed
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 5: Duplicate/Near-Duplicate Removal (5%)
# --------------------------------------------------------------------------- #


def task_05() -> int:
    """Remove duplicate/near-duplicate trips."""
    conn = get_connection()
    try:
        before = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        execute_sql_batch(get_sql(5), conn)
        after = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        return before - after
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 6: Speed Outlier Removal (10%)
# --------------------------------------------------------------------------- #


def task_06() -> int:
    """Remove trips with non-positive duration or speed > 120 km/h."""
    global task_06_explanation
    conn = get_connection()
    try:
        before = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        removed = execute_sql(f"""
            SELECT COUNT(*)
            FROM TripRecords
            WHERE EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) <= 0
               OR (trip_distance * {MILES_TO_KM}) / NULLIF(EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) / 3600.0, 0) > {MAX_SPEED_KPH}
        """, conn)[0][0]
        execute_sql(f"""
            DELETE FROM TripRecords
            WHERE EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) <= 0
               OR (trip_distance * {MILES_TO_KM}) / NULLIF(EXTRACT(EPOCH FROM (dropoff_dt - pickup_dt)) / 3600.0, 0) > {MAX_SPEED_KPH}
        """, conn)
        after = execute_sql("SELECT COUNT(*) FROM TripRecords", conn)[0][0]
        task_06_explanation = f"Before: {before}; after: {after}; removed: {removed}; speed=(miles*1.609)/hours."
        return removed
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 7: Pickup Volume During Time Window (5%)
# --------------------------------------------------------------------------- #


def task_07() -> dict[str, Any]:
    """Count trips in time window and identify the busiest pickup zone."""
    conn = get_connection()
    try:
        trips = execute_sql(f"""
            SELECT COUNT(*) FROM TripRecords
            WHERE pickup_dt >= '{WINDOW_START}' AND pickup_dt < '{WINDOW_END}'
        """, conn)[0][0]
        row = execute_sql(f"""
            SELECT tr.pu_location_id, tz.zone, COUNT(*) AS cnt
            FROM TripRecords tr
            LEFT JOIN TaxiZones tz ON tz.location_id = tr.pu_location_id
            WHERE tr.pickup_dt >= '{WINDOW_START}' AND tr.pickup_dt < '{WINDOW_END}'
            GROUP BY tr.pu_location_id, tz.zone
            ORDER BY cnt DESC
            LIMIT 1
        """, conn)[0]
        return {"trips_in_window": trips, "busiest_zone": row[1] or "", "busiest_zone_id": row[0], "busiest_count": row[2]}
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 8: Overlapping Pickup Pairs (10%)
# --------------------------------------------------------------------------- #


def task_08() -> int:
    """Find close pickup pairs within 50m and 5 minutes, without double counting."""
    global task_08_pairs
    conn = get_connection()
    try:
        rows = execute_sql(f"""
            SELECT a.trip_id, b.trip_id
            FROM TripRecords a
            JOIN TripRecords b
              ON a.trip_id < b.trip_id
             AND a.passenger_count > 0
             AND b.passenger_count > 0
             AND a.pickup_dt >= '{WINDOW_START}' AND a.pickup_dt < '{WINDOW_END}'
             AND b.pickup_dt >= '{WINDOW_START}' AND b.pickup_dt < '{WINDOW_END}'
             AND ABS(EXTRACT(EPOCH FROM (a.pickup_dt - b.pickup_dt))) <= 300
             AND ST_DWithin(a.pickup_location::geography, b.pickup_location::geography, 50)
        """, conn)
        task_08_pairs = [(int(a), int(b)) for a, b in rows]
        return len(task_08_pairs)
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 9: Per-Pickup-Zone Statistics (10%)
# --------------------------------------------------------------------------- #


def task_09() -> pd.DataFrame:
    """Compute per-pickup-zone distance and amount statistics."""
    conn = get_connection()
    try:
        return pd.read_sql_query(f"""
            SELECT pu_location_id,
                   MIN(trip_distance) AS min_trip_distance,
                   AVG(trip_distance) AS mean_trip_distance,
                   MAX(trip_distance) AS max_trip_distance,
                   MIN(total_amount) AS min_total_amount,
                   AVG(total_amount) AS mean_total_amount,
                   MAX(total_amount) AS max_total_amount,
                   COUNT(*) AS trip_count
            FROM TripRecords
            WHERE pickup_dt >= '{WINDOW_START}' AND pickup_dt < '{WINDOW_END}'
            GROUP BY pu_location_id
            ORDER BY pu_location_id
        """, conn)
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 10: Spatial Grid and Densest Cell (10%)
# --------------------------------------------------------------------------- #


def task_10() -> dict[str, int]:
    """Generate 2km grid, find densest pickup cell, and save map figure."""
    global task_10_grid_gdf

    import matplotlib.pyplot as plt

    conn = get_connection()
    try:
        pickups = gpd.read_postgis(
            f"""
            SELECT trip_id, pickup_location AS geometry
            FROM TripRecords
            WHERE pickup_dt >= '{WINDOW_START}' AND pickup_dt < '{WINDOW_END}'
              AND pickup_location IS NOT NULL
            """,
            conn,
            geom_col="geometry",
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        bbox_gdf = gpd.GeoDataFrame(
            geometry=[box(MIN_LON, MIN_LAT, MAX_LON, MAX_LAT)],
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        minx, miny, maxx, maxy = bbox_gdf.total_bounds

        cells = []
        cell_id = 1
        y = miny

        while y < maxy:
            x = minx
            while x < maxx:
                cells.append(
                    (
                        cell_id,
                        box(
                            x,
                            y,
                            min(x + 2000, maxx),
                            min(y + 2000, maxy),
                        ),
                    )
                )
                cell_id += 1
                x += 2000
            y += 2000

        grid = gpd.GeoDataFrame(
            cells,
            columns=["cell_id", "geometry"],
            crs="EPSG:3857",
        )

        joined = gpd.sjoin(
            pickups,
            grid,
            how="left",
            predicate="within",
        )

        counts = joined.groupby("cell_id").size().rename("pickup_count")

        grid = grid.merge(
            counts,
            on="cell_id",
            how="left",
        ).fillna({"pickup_count": 0})

        grid["pickup_count"] = grid["pickup_count"].astype(int)

        densest = grid.sort_values(
            ["pickup_count", "cell_id"],
            ascending=[False, True],
        ).iloc[0]

        densest_cell_id = int(densest.cell_id)
        densest_count = int(densest.pickup_count)

        task_10_grid_gdf = grid.to_crs(epsg=4326)

        # Find taxi zones shapefile for map background
        shp_files = list(DATA_DIR.glob("**/taxi_zones.shp")) + list(DATA_DIR.glob("**/*.shp"))

        if not shp_files:
            zip_path = DATA_DIR / "taxi_zones.zip"
            extract_dir = DATA_DIR / "taxi_zones"
            extract_dir.mkdir(exist_ok=True)

            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)

            shp_files = list(extract_dir.glob("**/taxi_zones.shp")) + list(
                extract_dir.glob("**/*.shp")
            )

        zones = gpd.read_file(shp_files[0]).to_crs(epsg=3857)

        sample = pickups.sample(
            n=min(20000, len(pickups)),
            random_state=42,
        )


        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm
        from matplotlib.ticker import FuncFormatter

        # Professional map styling
        fig, ax = plt.subplots(figsize=(12, 12), facecolor="#0b1220")
        ax.set_facecolor("#0b1220")

        # Pickup density per 2km x 2km grid cell
        active_grid = grid[grid["pickup_count"] > 0]

        active_grid.plot(
            ax=ax,
            column="pickup_count",
            cmap="YlOrRd",
            norm=LogNorm(
                vmin=1,
                vmax=max(2, int(grid["pickup_count"].max()))
            ),
            edgecolor="#243247",
            linewidth=0.25,
            legend=True,
            legend_kwds={
                "label": "Number of Pickups (log scale)",
                "shrink": 0.65
            }
        )

        # NYC Taxi Zone boundaries
        zones.boundary.plot(
            ax=ax,
            color="#94a3b8",
            linewidth=0.35,
            alpha=0.45
        )

        # Highlight densest grid cell
        densest_grid = grid[
            grid["cell_id"] == densest_cell_id
        ]

        densest_grid.boundary.plot(
            ax=ax,
            color="#22d3ee",
            linewidth=3
        )

        # Annotate densest cell
        center = densest_grid.geometry.iloc[0].centroid

        ax.annotate(
            f"Highest Density\n{densest_count:,} pickups",
            xy=(center.x, center.y),
            xytext=(35, 45),
            textcoords="offset points",
            color="white",
            fontsize=11,
            fontweight="bold",
            arrowprops={
                "arrowstyle": "->",
                "color": "#22d3ee",
                "lw": 1.5
            },
            bbox={
                "boxstyle": "round,pad=0.5",
                "fc": "#162238",
                "ec": "#22d3ee",
                "alpha": 0.95
            }
        )

        # Title and subtitle
        ax.set_title(
            "NYC YELLOW TAXI | PICKUP DENSITY\n"
            "Spatial Grid Analysis (2 km x 2 km)",
            fontsize=19,
            fontweight="bold",
            color="white",
            pad=25
        )

        ax.text(
            0.5, 0.02,
            "January 15, 2026 | 12:00 - 14:00",
            transform=ax.transAxes,
            ha="center",
            fontsize=11,
            color="#cbd5e1"
        )

        # Style colorbar
        for other_ax in fig.axes:
            if other_ax is not ax:
                other_ax.tick_params(colors="white")
                other_ax.yaxis.label.set_color("white")

        ax.set_axis_off()
        ax.set_aspect("equal")

        plt.savefig(
            "task10_map.png",
            dpi=300,
            bbox_inches="tight",
            facecolor=fig.get_facecolor()
        )

        plt.close()

        return {
            "densest_cell_id": densest_cell_id,
            "densest_count": densest_count,
            "grid_cells": int(len(grid)),
        }

    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 11: Trips and TaxiZones Tables (10%)
# --------------------------------------------------------------------------- #


def task_11() -> dict[str, int]:
    """Create enriched Trips and TaxiZonesFull polygon tables."""
    conn = get_connection()
    try:
        execute_sql_batch(get_sql(11), conn)

        shp_files = list(DATA_DIR.glob("**/taxi_zones.shp")) + list(DATA_DIR.glob("**/*.shp"))
        if not shp_files:
            zip_path = DATA_DIR / "taxi_zones.zip"
            extract_dir = DATA_DIR / "taxi_zones"
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
            shp_files = list(extract_dir.glob("**/taxi_zones.shp")) + list(extract_dir.glob("**/*.shp"))
        shp = shp_files[0]

        zones = gpd.read_file(shp).to_crs(epsg=4326)
        id_col = "LocationID" if "LocationID" in zones.columns else "location_id"
        zones = zones.rename(columns={id_col: "location_id"})
        lookup = pd.read_csv(DATA_DIR / "taxi_zone_lookup.csv").rename(columns={
            "LocationID": "location_id", "Borough": "borough", "Zone": "zone"
        })
        zones = zones.merge(lookup[["location_id", "borough", "zone", "service_zone"]], on="location_id", how="left")

        # If the shapefile already had similarly named fields, pandas creates _x/_y suffixes.
        borough_col = "borough" if "borough" in zones.columns else "borough_x"
        zone_col = "zone" if "zone" in zones.columns else "zone_x"
        service_col = "service_zone" if "service_zone" in zones.columns else "service_zone_x"

        zone_rows = []
        for _, row in zones.iterrows():
            zone_rows.append((
                int(row["location_id"]),
                None if pd.isna(row[borough_col]) else row[borough_col],
                None if pd.isna(row[zone_col]) else row[zone_col],
                None if pd.isna(row[service_col]) else row[service_col],
                row["geometry"].wkt,
            ))
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE TaxiZonesFull;")
            execute_values(cur, """
                INSERT INTO TaxiZonesFull (location_id, borough, zone, service_zone, geometry)
                VALUES %s
            """, zone_rows, template="(%s, %s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromText(%s), 4326)))", page_size=1000)
        conn.commit()
        trips_rows = execute_sql("SELECT COUNT(*) FROM Trips", conn)[0][0]
        taxizones_rows = execute_sql("SELECT COUNT(*) FROM TaxiZonesFull", conn)[0][0]
        return {"trips_rows": trips_rows, "taxizones_rows": taxizones_rows}
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 12: Top Origin-Destination Zone Pairs (10%)
# --------------------------------------------------------------------------- #


def task_12() -> pd.DataFrame:
    """Return top 5 origin-destination zone pairs."""
    conn = get_connection()
    try:
        return pd.read_sql_query("""
            SELECT pu.zone AS pickup_zone,
                   do_z.zone AS dropoff_zone,
                   COUNT(*) AS trip_count
            FROM Trips t
            JOIN TaxiZonesFull pu ON ST_Within(t.pickup_location, pu.geometry)
            JOIN TaxiZonesFull do_z ON ST_Within(t.dropoff_location, do_z.geometry)
            GROUP BY pu.zone, do_z.zone
            ORDER BY trip_count DESC
            LIMIT 5
        """, conn)
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 13: Per-Zone Tip and Passenger Analysis (5%)
# --------------------------------------------------------------------------- #


def task_13() -> pd.DataFrame:
    """Average tip and percentage of trips with passenger_count >= 2 by pickup zone."""
    conn = get_connection()
    try:
        return pd.read_sql_query("""
            SELECT z.zone AS pickup_zone,
                   z.borough AS pickup_borough,
                   AVG(t.tip_amount) AS avg_tip,
                   100.0 * AVG(CASE WHEN t.passenger_count >= 2 THEN 1.0 ELSE 0.0 END) AS trips_with_passengers_pct,
                   COUNT(*) AS total_trips
            FROM Trips t
            JOIN TaxiZonesFull z ON ST_Within(t.pickup_location, z.geometry)
            GROUP BY z.zone, z.borough
            ORDER BY z.borough, z.zone
        """, conn)
    finally:
        close_connection(conn)


# --------------------------------------------------------------------------- #
# Task 14 (Bonus): Fare Prediction (10%)
# --------------------------------------------------------------------------- #


def task_14() -> dict[str, Any]:
    """Train a simple fare prediction model and report MAE."""
    global task_14_model
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT pu_location_id, do_location_id, passenger_count, trip_distance,
                   EXTRACT(HOUR FROM pickup_dt) AS pickup_hour,
                   EXTRACT(DOW FROM pickup_dt) AS pickup_dow,
                   total_amount
            FROM TripRecords
            WHERE total_amount IS NOT NULL
            AND total_amount > 0
            AND passenger_count IS NOT NULL
            AND trip_distance IS NOT NULL
            AND pickup_dt IS NOT NULL
        """, conn)
    finally:
        close_connection(conn)

    if len(df) < 10:
        task_14_model = None
        return {"model_name": "NotEnoughData", "mae": 0.0, "n_features": 0}

    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import mean_absolute_error
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder

        X = df.drop(columns=["total_amount"])
        y = df["total_amount"]
        categorical = ["pu_location_id", "do_location_id"]
        numeric = ["passenger_count", "trip_distance", "pickup_hour", "pickup_dow"]
        preprocessor = ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("num", SimpleImputer(strategy="median"), numeric),
        ])
        model = Pipeline([
            ("preprocess", preprocessor),
            ("model", RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1, max_depth=18)),
        ])
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.20, random_state=42)
        model.fit(X_train, y_train)
        pred = model.predict(X_test)
        mae = float(mean_absolute_error(y_test, pred))
        task_14_model = model
        return {"model_name": "RandomForestRegressor", "mae": mae, "n_features": 6}
    except ModuleNotFoundError:
        import numpy as np

        work = df.copy()
        for col in ["passenger_count", "trip_distance", "pickup_hour", "pickup_dow", "total_amount"]:
            work[col] = pd.to_numeric(work[col], errors="coerce")
        work = work.dropna().sample(frac=1.0, random_state=42).reset_index(drop=True)
        split = max(1, int(len(work) * 0.8))
        train, test = work.iloc[:split], work.iloc[split:]
        if test.empty:
            test = train

        top_pu = train["pu_location_id"].value_counts().head(25).index.tolist()
        top_do = train["do_location_id"].value_counts().head(25).index.tolist()

        def design(frame: pd.DataFrame) -> Any:
            parts = [np.ones((len(frame), 1))]
            parts.append(frame[["passenger_count", "trip_distance", "pickup_hour", "pickup_dow"]].astype(float).to_numpy())
            for loc in top_pu:
                parts.append((frame["pu_location_id"].to_numpy() == loc).astype(float).reshape(-1, 1))
            for loc in top_do:
                parts.append((frame["do_location_id"].to_numpy() == loc).astype(float).reshape(-1, 1))
            return np.hstack(parts)

        X_train = design(train)
        y_train = train["total_amount"].astype(float).to_numpy()
        coef = np.linalg.pinv(X_train) @ y_train
        pred = design(test) @ coef
        y_test = test["total_amount"].astype(float).to_numpy()
        mae = float(np.mean(np.abs(y_test - pred)))
        task_14_model = {"coef": coef, "top_pu": top_pu, "top_do": top_do}
        return {"model_name": "NumpyLinearRegressionFallback", "mae": mae, "n_features": int(X_train.shape[1] - 1)}


# --------------------------------------------------------------------------- #
# Main: run all tasks (for local testing)
# --------------------------------------------------------------------------- #


def run_task(task_num: int) -> Any:
    """Run a single task by number, for local testing."""
    task_map: dict[int, Any] = {
        1: task_01,
        2: task_02,
        3: task_03,
        4: task_04,
        5: task_05,
        6: task_06,
        7: task_07,
        8: task_08,
        9: task_09,
        10: task_10,
        11: task_11,
        12: task_12,
        13: task_13,
        14: task_14,
    }
    fn = task_map.get(task_num)
    if fn is None:
        raise ValueError(f"Invalid task number: {task_num}. Must be 1–14.")
    return fn()


def task_main() -> dict[str, str]:
    """Run all tasks in order and print results summary."""
    tasks: list[tuple[str, Any]] = [
        ("Task 01", task_01),
        ("Task 02", task_02),
        ("Task 03", task_03),
        ("Task 04", task_04),
        ("Task 05", task_05),
        ("Task 06", task_06),
        ("Task 07", task_07),
        ("Task 08", task_08),
        ("Task 09", task_09),
        ("Task 10", task_10),
        ("Task 11", task_11),
        ("Task 12", task_12),
        ("Task 13", task_13),
        ("Task 14", task_14),
    ]

    results: dict[str, str] = {}
    for name, fn in tasks:
        try:
            result = fn()
            results[name] = f"OK -> {result}"
        except Exception as e:
            results[name] = f"ERROR: {e}"
        print(f"{name}: {results[name]}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CDS209 NYC Yellow Taxi Submission")
    parser.add_argument("--task", type=int, help="Run a single task (1-14) instead of all")
    args = parser.parse_args()

    if args.task:
        result = run_task(args.task)
        print(f"Task {args.task}: {result}")
    else:
        task_main()
