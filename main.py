from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from fastapi.responses import JSONResponse
import json
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to your Vercel domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load .gdb file once
# gdb_path = "data/trails.gdb"
# trails = gpd.read_file(gdb_path)

# @app.get("/trails")
# def get_trails(lat: float = Query(...), lon: float = Query(...)):
#     point = Point(lon, lat)
#     buffer = point.buffer(0.001)
#     nearby = trails[trails.intersects(buffer)]
#     return nearby.to_json()

#####################################################################

trails = gpd.read_file("data/MA_State_Trails.geojson")
print("Trail Data Loaded!")

## HELPER FUNCTION ##
def get_trail_by_name(name):
    return trails[trails["name"] == name]

#Set global CRS
PROJECTION = trails.crs

@app.get("/trails_by_name")
def get_trail_by_name(name: str = Query(..., description="Exact trail name to search for")):
    result = trails[trails["name"].str.lower() == name.lower()]
    if result.empty:
        return {"message": f"No trail found with name '{name}'."}
    
    # Drop all datetime columns
    datetime_cols = [col for col in result.columns if result[col].dtype.kind in "Mm"]  # M = datetime64, m = timedelta
    result = result.drop(columns=datetime_cols)

    return result.to_json()

@app.get("/species_by_trail")
def get_species_by_trail(
    trail_name: str = Query(..., description="Name of the trail"),
    current_month: int = Query(..., ge=1, le=12, description="Current month as integer 1-12")
):
    TRAIL_BUFFER = 200 #meters

    #Select trail
    trail = get_trail_by_name(trail_name)

    # trail.plot()
    #Add buffer:
    trail = trail.to_crs(epsg=5070) #to metered projection
    trail = gpd.GeoDataFrame(geometry=trail.buffer(TRAIL_BUFFER), crs=trail.crs)
    trail = trail.dissolve()
    trail = trail.to_crs(crs=PROJECTION) #back to original CRS
    # trail.plot()

    #Get bounding box
    SW_Lng, SW_Lat, NE_Lng, NE_Lat = trail.total_bounds
    # print(f"Bounding Box, SW: ({SW_Lng}, {SW_Lat}) NE: ({NE_Lng}, {NE_Lat})")

    # from datetime import datetime
    curr_month = current_month # a number
    if curr_month == 1:
        last_month = 12
    else:
        last_month = curr_month - 1
    if curr_month == 12:
        next_month = 1
    else:
        next_month = curr_month + 1

    iNat_url = "https://api.inaturalist.org/v1/observations"
    params = {
        "swlng": SW_Lng,
        "swlat": SW_Lat,
        "nelng": NE_Lng,
        "nelat": NE_Lat,      
        "quality_grade": "research",
        "per_page": 200,
        "page": 1,
        "month": [last_month, curr_month, last_month],        
        "year": [2022, 2023, 2024],     # last 3 years
        "order_by": "observed_on"
    }

    r = requests.get(iNat_url, params=params)
    
    data = r.json()
    observations = pd.json_normalize(data["results"])
    species = observations["species_guess"]
    # display(species)    
    return species.to_json()

