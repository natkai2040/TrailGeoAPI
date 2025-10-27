from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import geopandas as gpd
from shapely.geometry import Point

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to your Vercel domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load .gdb file once
gdb_path = "data/trails.gdb"
trails = gpd.read_file(gdb_path)

@app.get("/trails")
def get_trails(lat: float = Query(...), lon: float = Query(...)):
    point = Point(lon, lat)
    buffer = point.buffer(0.001)
    nearby = trails[trails.intersects(buffer)]
    return nearby.to_json()
