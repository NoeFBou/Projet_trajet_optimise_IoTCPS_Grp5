import requests
import os
import math
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Tuple
from ortools.constraint_solver import pywrapcp
from ortools.constraint_solver import routing_enums_pb2

app = FastAPI(title="service VRP")


# --- MODÈLES ---
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
    depot: Tuple[float, float]


# --- CONFIG ---
OSRM_URL = os.getenv("OSRM_URL", "http://osrm-backend:5000")


# --- OUTILS MATHÉMATIQUES (FALLBACK) ---
# ... (imports et config restent pareils)

def haversine_duration(lat1, lon1, lat2, lon2, speed_kmh=30.0):
    """Fallback mathématique : distance vol d'oiseau"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_meters = R * c

    return int((dist_meters * 1.4) / (speed_kmh / 3.6))


def get_time_matrix(locations: List[Tuple[float, float]], chunk_size: int = 50) -> List[List[int]]:
    n = len(locations)
    matrix = [[0] * n for _ in range(n)]

    print(f"[VRP] Calcul matrice pour {n} points (Mode résilient)...")

    for i in range(0, n, chunk_size):
        for j in range(0, n, chunk_size):
            src_batch = locations[i: i + chunk_size]
            dst_batch = locations[j: j + chunk_size]

            # 1. ARRONDI DES COORDONNÉES (Pour éviter URL trop longue/malformed)
            # On arrondit à 6 décimales (~10cm de précision), suffisant et bien plus court
            src_coords_str = [f"{round(lon, 6)},{round(lat, 6)}" for lon, lat in src_batch]
            dst_coords_str = [f"{round(lon, 6)},{round(lat, 6)}" for lon, lat in dst_batch]

            # Préparation URL OSRM
            if i == j:
                all_coords = src_coords_str
                src_idx = list(range(len(src_batch)))
                dst_idx = src_idx
            else:
                all_coords = src_coords_str + dst_coords_str
                src_idx = list(range(len(src_batch)))
                dst_idx = list(range(len(src_batch), len(src_batch) + len(dst_batch)))

            coords_url_part = ";".join(all_coords)
            src_url_part = ";".join(map(str, src_idx))
            dst_url_part = ";".join(map(str, dst_idx))

            # 2. PARAMÈTRE RADIUSES (La solution magique)
            # On génère une liste "1000;1000;1000..." pour dire "cherche à 1km à la ronde pour chaque point"
            radiuses_param = ";".join(["1000"] * len(all_coords))

            full_url = (f"{OSRM_URL}/table/v1/driving/{coords_url_part}"
                        f"?sources={src_url_part}&destinations={dst_url_part}"
                        f"&annotations=duration&radiuses={radiuses_param}")

            try:
                # Timeout court : si OSRM galère plus de 2s, on passe en mathématique
                response = requests.get(full_url, timeout=2.0)

                if response.status_code == 200:
                    data = response.json()
                    # Vérification code OSRM
                    if data.get("code") != "Ok":
                        # Si OSRM dit "NoSegment" malgré le rayon de 1000m, on force le fallback
                        raise Exception(f"OSRM Logic Error: {data.get('code')}")

                    durations = data.get("durations", [])
                    for r, row in enumerate(durations):
                        for c, val in enumerate(row):
                            if val is None:
                                # Même avec le rayon, pas de route trouvée -> Math
                                s_lat, s_lon = src_batch[r][1], src_batch[r][0]
                                d_lat, d_lon = dst_batch[c][1], dst_batch[c][0]
                                matrix[i + r][j + c] = haversine_duration(s_lat, s_lon, d_lat, d_lon)
                            else:
                                matrix[i + r][j + c] = int(val)
                else:
                    raise Exception(f"HTTP {response.status_code}")

            except Exception as e:
                # 3. FALLBACK MATHÉMATIQUE (Si tout échoue)
                # On ne log que les erreurs critiques pour ne pas spammer
                # print(f"[VRP] Fallback Math sur chunk {i}-{j} ({e})")
                for r_idx, (src_lon, src_lat) in enumerate(src_batch):
                    for c_idx, (dst_lon, dst_lat) in enumerate(dst_batch):
                        matrix[i + r_idx][j + c_idx] = haversine_duration(src_lat, src_lon, dst_lat, dst_lon)

    return matrix



# --- ENDPOINT ---
@app.post("/solve_vrp")
def solve_vrp(request: VRPRequest):
    print(f"[VRP] Reçu demande: {len(request.bins)} poubelles, {len(request.trucks)} camions.")

    # Préparation des données
    depot_lon, depot_lat = request.depot[1], request.depot[0]
    all_coordinates = [(depot_lon, depot_lat)]
    for b in request.bins:
        all_coordinates.append((b.lon, b.lat))

    # Calcul Matrice (Indestructible maintenant)
    time_matrix = get_time_matrix(all_coordinates)

    # Configuration OR-Tools
    num_locations = len(all_coordinates)
    num_vehicles = len(request.trucks)
    depot_index = 0

    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    # Coûts (Temps)
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Contrainte Temps (24h max)
    routing.AddDimension(
        transit_callback_index,
        3600,  # Slack (attente autorisée)
        86400,  # Max cumul (24h)
        True,  # Start at zero
        "Time"
    )

    # Contrainte Capacité
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        if from_node == 0: return 0
        return int(request.bins[from_node - 1].weight)

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    truck_capacities = [int(t.capacity) for t in request.trucks]

    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        truck_capacities,
        True,
        "Capacity"
    )

    # Disjonction (Pénalité si non visité)
    penalty = 1000000
    for node in range(1, num_locations):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    # Recherche
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    search_parameters.time_limit.seconds = 30
    # search_parameters.log_search = True # Décommenter pour debug intense

    solution = routing.SolveWithParameters(search_parameters)

    if solution:
        routes = []
        total_time = 0
        dropped = 0

        # Compte les ignorés
        for node in range(routing.Size()):
            if routing.IsStart(node) or routing.IsEnd(node): continue
            if solution.Value(routing.NextVar(node)) == node: dropped += 1

        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            if routing.IsEnd(solution.Value(routing.NextVar(index))): continue

            route_points = []
            route_load = 0
            route_time = 0
            truck_id = request.trucks[vehicle_id].id

            while not routing.IsEnd(index):
                node_index = manager.IndexToNode(index)

                if node_index == 0:
                    point_id = "DEPOT"
                    lat, lon = request.depot
                else:
                    b = request.bins[node_index - 1]
                    point_id, lat, lon = b.id, b.lat, b.lon
                    route_load += b.weight

                previous_index = index
                index = solution.Value(routing.NextVar(index))
                route_time += routing.GetArcCostForVehicle(previous_index, index, vehicle_id)

                route_points.append({
                    "point_id": point_id,
                    "lat": lat, "lon": lon,
                    "load_after_visit": route_load
                })

            # Retour dépôt
            route_points.append({
                "point_id": "DEPOT_RETURN",
                "lat": request.depot[0], "lon": request.depot[1],
                "final_load": route_load
            })

            routes.append({
                "truck_id": truck_id,
                "total_time_seconds": route_time,
                "total_load": route_load,
                "stops": route_points
            })
            total_time += route_time

        return {"status": "optimized", "total_fleet_time": total_time, "dropped_bins": dropped, "routes": routes}
    else:
        return {"status": "failed", "message": "Aucune solution trouvée."}