from typing import List
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from fastapi.responses import JSONResponse
import json
from pyproj import CRS
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


@app.get("/trails_search")
def search_trails_by_name(
    q: str = Query(..., description="Partial trail name to search for")
):
    """Return a list of trails whose names contain the search string."""
    # Case-insensitive substring search
    matches = trails[trails["name"].str.contains(q, case=False, na=False)]

    if matches.empty:
        return {"message": f"No trails found matching '{q}'."}

    # Drop datetime columns (avoid serialization issues)
    datetime_cols = [col for col in matches.columns if matches[col].dtype.kind in "Mm"]
    matches = matches.drop(columns=datetime_cols)

    # Simplify result — return only key info
    simplified = matches[["name", "geometry"]].copy()
    simplified["geometry"] = simplified["geometry"].astype(str)

    # Convert to list of dicts
    results_list = simplified.to_dict(orient="records")

    return {"count": len(results_list), "results": results_list}

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

# example request: /extended_trail_search?current_month=5&include_ids=123,456&exclude_ids=789&trail_name=Blue%20Trail
@app.get("/extended_trail_search")
def get_trail_by_species(
    current_month: int = Query(..., ge=1, le=12, description="Current month as integer 1-12"),
    include_ids: List[str] = Query(None, description="List of species IDs to include"),
    exclude_ids: List[int] = Query(None, description="List of species IDs to exclude"),
    trail_name: str = Query('', description="Exact trail name to search for")
):
    #Filter trail by name
    if (trail_name != ''):
        trail = get_trail_by_name(trails, trail_name)
    else:
        trail = trails

    #Construct include and exclude buffers
    buffers = []
    for flagged_list in [include_ids, exclude_ids]:
        if len(flagged_list) == 0:
            buffers.append(-2) #Indicating that no buffer was generated because no species were specified
            continue
        print("Generating Buffer")
        json = species_search(flagged_list, current_month, "https://api.inaturalist.org/v1/grid/7/38/47.grid.json")
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
    return filtered_trail

