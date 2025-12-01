import requests
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Tuple
from ortools.constraint_solver import pywrapcp
from ortools.constraint_solver import routing_enums_pb2

app = FastAPI(title="service VRP")

class Bin(BaseModel):
    id: str
    lat: float
    lon: float
    weight: float

class Truck(BaseModel):
    id: str
    capacity: float

class VRPRequest(BaseModel):
    bins: List[Bin]
    trucks: List[Truck]
    depot: Tuple[float, float] # (Latitude, Longitude)


#cinfig
OSRM_URL = os.getenv("OSRM_URL", "http://osrm-backend:5000")


def get_time_matrix(locations: List[Tuple[float, float]]) -> List[List[int]]:
    """
    locations: Une liste de tuples (Longitude, Latitude)
    OSRM prend (Lon, Lat)
    """
    coordinates_str = ";".join([f"{lon},{lat}" for lon, lat in locations])

    full_url = f"{OSRM_URL}/table/v1/driving/{coordinates_str}?annotations=duration"

    try:
        response = requests.get(full_url)
        data = response.json()

        if data.get("code") != "Ok":
            print(f"Erreur OSRM detail: {data}")
            raise HTTPException(status_code=500, detail=f"OSRM error: {data.get('message')}")

        durations = data["durations"]
        return [[int(round(x)) for x in row] for row in durations]

    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=500, detail="Cannot connect to OSRM server. Check docker network.")


@app.post("/solve_vrp")
def solve_vrp(request: VRPRequest):

    depot_lon = request.depot[1]
    depot_lat = request.depot[0]

    all_coordinates = [(depot_lon, depot_lat)]

    for b in request.bins:
        all_coordinates.append((b.lon, b.lat))

    time_matrix = get_time_matrix(all_coordinates)

    num_locations = len(all_coordinates)
    num_vehicles = len(request.trucks)
    depot_index = 0

    truck_capacities = [t.capacity for t in request.trucks]

    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    routing.AddDimension(
        transit_callback_index,
        3000,
        86400,
        True,
        "Time"
    )

    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)

        if from_node == 0:
            return 0

        return request.bins[from_node - 1].weight

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)

    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        truck_capacities,
        True,
        "Capacity"
    )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    search_parameters.time_limit.seconds = 10

    solution = routing.SolveWithParameters(search_parameters)

    if solution:
        routes = []
        total_time = 0

        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            route_points = []
            route_load = 0
            route_time = 0

            truck_id = request.trucks[vehicle_id].id

            while not routing.IsEnd(index):
                node_index = manager.IndexToNode(index)

                if node_index == 0:
                    point_id = "DEPOT"
                    lat, lon = request.depot
                    weight = 0
                else:
                    bin_obj = request.bins[node_index - 1]
                    point_id = bin_obj.id
                    lat, lon = bin_obj.lat, bin_obj.lon
                    weight = bin_obj.weight

                route_load += weight

                previous_index = index
                index = solution.Value(routing.NextVar(index))

                segment_time = routing.GetArcCostForVehicle(previous_index, index, vehicle_id)
                route_time += segment_time

                route_points.append({
                    "point_id": point_id,
                    "lat": lat,
                    "lon": lon,
                    "load_after_visit": route_load
                })

            route_points.append({
                "point_id": "DEPOT_RETURN",
                "lat": request.depot[0],
                "lon": request.depot[1],
                "final_load": route_load
            })

            if len(route_points) > 2:
                routes.append({
                    "truck_id": truck_id,
                    "total_time_seconds": route_time,
                    "total_load": route_load,
                    "stops": route_points
                })
                total_time += route_time

        return {"status": "optimized", "total_fleet_time": total_time, "routes": routes}
    else:
        return {"status": "failed", "message": "Aucune solution trouvée."}