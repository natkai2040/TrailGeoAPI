from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import geopandas as gpd
from shapely.geometry import Point
from fastapi.responses import JSONResponse
import json

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
    geojson_str = result.to_json()
    geojson = json.loads(geojson_str)  # convert string → dict
    return JSONResponse(content=geojson)
