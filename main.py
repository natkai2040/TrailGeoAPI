from typing import List, Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from fastapi.responses import JSONResponse
import json
from pyproj import CRS
import requests
import os

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


#Set global CRS
PROJECTION = trails.crs

def get_trail_by_name_internal(name: str):
    result = trails[trails["name"].str.lower() == name.lower()]
    if result.empty:
        return {"message": f"No trail found with name '{name}'."}
    # Drop all datetime columns
    datetime_cols = [col for col in result.columns if result[col].dtype.kind in "Mm"]  # M = datetime64, m = timedelta
    result = result.drop(columns=datetime_cols)

    return result.to_json()

@app.get("/trails_by_name")
def get_trail_by_name(name: str = Query(..., description="Exact trail name to search for")):
    return get_trail_by_name_internal(name)

## HELPER FUNCTION ##
def get_trail_by_name_helper(name):
    return trails[trails["name"] == name]

@app.get("/species_by_trail")
def get_species_by_trail(
    trail_name: str = Query(..., description="Name of the trail"),
    current_month: int = Query(..., ge=1, le=12, description="Current month as integer 1-12")
):
    TRAIL_BUFFER = 200 #meters

    #Select trail
    trail = get_trail_by_name_helper(trail_name)

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
        "month": [last_month, curr_month, next_month],        
        "year": [2022, 2023, 2024],     # last 3 years
        "order_by": "observed_on"
    }

    r = requests.get(iNat_url, params=params)
    data = r.json()
    if data['total_results'] == 0: # No species found
        return []
    observations = pd.json_normalize(data["results"])
    species = observations[["species_guess", "taxon.default_photo.medium_url", "taxon.preferred_common_name"]]
    # display(species)    
    return species.to_json()

def search_trails_gdf(q: str): # returns GeoDataFrame
    matches = trails[trails["name"].str.contains(q, case=False, na=False)]

    if matches.empty:
        return gpd.GeoDataFrame(columns=trails.columns, crs=trails.crs)

    datetime_cols = [col for col in matches.columns if matches[col].dtype.kind in "Mm"]
    matches = matches.drop(columns=datetime_cols)

    return matches

@app.get("/trails_search")
def search_trails_by_name(
    q: str = Query(..., description="Partial trail name to search for")
):
    gdf = search_trails_gdf(q)

    if gdf.empty:
        return {"message": f"No trails found matching '{q}'."}

    # Convert trails to Dict
    results = gdf[["name", "geometry"]].copy()
    results["geometry"] = results["geometry"].astype(str)

    return {
        "count": len(results),
        "results": results.to_dict(orient="records")
    }

def species_search(species_list, current_month, iNat_endpoint, place_ID = 2):
    '''Helper function to run iNaturalist API queries. Default search is within place_ID=2 (MA). Returns JSON'''
    #Get adjecent months
    curr_month = current_month
    if curr_month == 1:
        last_month = 12
    else:
        last_month = curr_month - 1
    if curr_month == 12:
        next_month = 1
    else:
        next_month = curr_month + 1
    #Construct iNat request
    params = {
        "place_id": place_ID,
        "quality_grade": "research",
        "per_page": 200,
        "page": 1,
        "month": [last_month, curr_month, next_month],        
        "year": [2022, 2023, 2024],     # last 3 years
        "order_by": "observed_on",
        "taxon_id": species_list
    }
    r = requests.get(iNat_endpoint, params=params)
    print(r.url)
    return r.json()

def iNatGrid_to_coords(json):
    '''Helper. Converts iNaturalist Grid json into a list of coordinates [long, lat]'''
    json_info = json["data"]
    coordinate_list = []
    for data in json_info.values():
        coordinate = [data["longitude"], data["latitude"]]
        coordinate_list.append(coordinate)
    return coordinate_list

def get_buffer(coordinate_list):
    '''Helper, makes buffers'''
    BUFFER = 5000
    points = [Point(lon, lat) for lon, lat in coordinate_list]
    gdf = gpd.GeoDataFrame(geometry=points, crs=PROJECTION)
    gdf = gdf.to_crs(5070) #To metered projection
    gdf = gdf.geometry.buffer(BUFFER) #Buffer by 200 meters
    dissolved = gdf.union_all() #Combines all the geometries into one file
    return gpd.GeoDataFrame(geometry=[dissolved], crs=5070).to_crs(PROJECTION)

def normalize_id_list(items):
    if not items:
        return []

    output = []
    for item in items:
        if item:  # ignore empty strings
            if "," in item:
                output.extend(x.strip() for x in item.split(",") if x.strip())
            else:
                output.append(item.strip())

    return output

# example request: /extended_trail_search?current_month=5&include_ids=123,456&exclude_ids=789&trail_name=Blue%20Trail
@app.get("/extended_trail_search")
def get_trail_by_species(
    current_month: Optional[int] = Query(..., ge=1, le=12, description="Current month as integer 1-12"),
    include_ids: Optional[List[str]] = Query(None, description="Comma seperated list of species IDs to include"),
    exclude_ids: Optional[List[str]] = Query(None, description="Comma seperated list of species IDs to exclude"),
    q: Optional[str] = Query('', description="Partial trail name to search for or empty for all trails")
):
    include_ids = normalize_id_list(include_ids)
    exclude_ids = normalize_id_list(exclude_ids)
    #Filter trail by name
    if (q != ''):
        trail = search_trails_gdf(q)
    else:
        trail = trails

    buffers = []
    for flagged_list in [include_ids, exclude_ids]:
        if not flagged_list:  #can put parameter as None or empty list
            buffers.append(-2) # Placeholder for no buffer
            continue

        print("Generating Buffer")
        json = species_search(flagged_list, current_month,
                            "https://api.inaturalist.org/v1/grid/7/38/47.grid.json")
        coords = iNatGrid_to_coords(json)
        buffer = get_buffer(coords)
        buffers.append(buffer)
    
    #Filter trails by the generated buffers
    if type(buffers[0]) == int: #If no species in include list
        filtered_trail = trail
    else:
        in_mask = trail.intersects(buffers[0].geometry.iloc[0])
        filtered_trail = trail[in_mask].copy()
    if type(buffers[1]) != int: #If no species in exclude list skip code below
        out_mask = ~filtered_trail.intersects(buffers[1].geometry.iloc[0])
        filtered_trail = filtered_trail[out_mask].copy()

    safe = filtered_trail.copy()

    safe["geometry"] = safe["geometry"].astype(str) # convert safely to strings.

    return {
        "count": len(safe),
        "results": safe.to_dict(orient="records")
    }

from supabase import create_client, Client

supabase = create_client(os.environ.get("NEXT_PUBLIC_SUPABASE_URL"), os.environ.get("NEXT_SUPABASE_SERVICE_KEY"))

PROJECTION = "EPSG:4326"  

def get_trail_by_id(trail_id: str) -> gpd.GeoDataFrame:
    """Fetch trail geometry from Supabase by ID."""
    res = supabase.table("custom_trails").select("*").eq("id", trail_id).execute()
    if not res.data:
        return gpd.GeoDataFrame(columns=["geometry"], crs=PROJECTION)

    features = res.data[0]["features"]
    # handle either FeatureCollection or single Feature
    if isinstance(features, dict) and features.get("type") == "FeatureCollection":
        gdf = gpd.GeoDataFrame.from_features(features["features"], crs=PROJECTION)
    else:
        gdf = gpd.GeoDataFrame.from_features([features], crs=PROJECTION)

    return gdf

@app.get("/species_by_trail_by_id") # duplicate of species_by_trail but uses trail ID from supabase
def get_species_by_trail_by_id(
    trail_id: str = Query(..., description="ID of the trail in supabase"),
    current_month: int = Query(..., ge=1, le=12, description="Current month as integer 1-12")
):
    TRAIL_BUFFER = 200  # meters

    # Fetch trail geometry from Supabase
    trail = get_trail_by_id(trail_id)
    if trail.empty:
        return []

    # Buffer the trail
    trail = trail.to_crs(epsg=5070)  # to metered projection
    trail = gpd.GeoDataFrame(geometry=trail.buffer(TRAIL_BUFFER), crs=trail.crs)
    trail = trail.dissolve()
    trail = trail.to_crs(crs=PROJECTION)

    # Bounding box
    SW_Lng, SW_Lat, NE_Lng, NE_Lat = trail.total_bounds

    # Adjacent months
    curr_month = current_month
    last_month = 12 if curr_month == 1 else curr_month - 1
    next_month = 1 if curr_month == 12 else curr_month + 1

    # iNaturalist API
    iNat_url = "https://api.inaturalist.org/v1/observations"
    params = {
        "swlng": SW_Lng,
        "swlat": SW_Lat,
        "nelng": NE_Lng,
        "nelat": NE_Lat,
        "quality_grade": "research",
        "per_page": 200,
        "page": 1,
        "month": [last_month, curr_month, next_month],
        "year": [2022, 2023, 2024],
        "order_by": "observed_on"
    }

    r = requests.get(iNat_url, params=params)
    data = r.json()
    if data.get("total_results", 0) == 0:
        return []

    observations = pd.json_normalize(data["results"])
    species = observations[[
        "species_guess",
        "taxon.default_photo.medium_url",
        "taxon.preferred_common_name"
    ]]

    return species.to_json()