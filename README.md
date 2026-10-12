
# NYC Yellow Taxi | Geospatial Analytics

Geospatial analysis of NYC Yellow Taxi trips using Python, PostgreSQL, PostGIS and GeoPandas.

## Project Overview

This project explores spatial and temporal patterns in New York City's Yellow Taxi trip data.

The analysis focuses on data cleaning, spatial queries, taxi pickup hotspots, and grid-based analysis.

## Technologies

- Python
- PostgreSQL / PostGIS
- Pandas & GeoPandas
- Shapely
- Matplotlib
- SQL

## Key Features

- Processing of 3.7M+ taxi trip records
- Data cleaning and validation
- Geospatial analysis using PostGIS
- Spatial grid analysis (2 km × 2 km)
- Identification of high-demand pickup areas
- Geographic data visualization

## Pickup Density Visualization

![NYC Taxi Pickup Density](images/task10_map.png)

**Spatial analysis results:**
- Densest grid cell: 656
- Pickups in densest cell: 1,378
- Total grid cells: 1,056

*Note: Pickup locations are approximated using taxi-zone centroids rather than individual GPS coordinates.*

## Repository Structure

- `submission.py` — Python analysis and visualization
- `queries.sql` — SQL and spatial queries
- `images/` — Generated visualizations
- `docs/` — Project report
- `requirements.txt` — Python dependencies

## Getting Started

Install the dependencies:

```bash
pip install -r requirements.txt
```

The analysis requires a configured PostgreSQL/PostGIS database populated with the relevant NYC Yellow Taxi data.

## Academic Context

Developed as part of the Geospatial Data Analysis coursework at the University of Piraeus, MSc in Cybersecurity & Data Science.
