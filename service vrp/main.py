"""
Service VRP (Vehicle Routing Problem)
-------------------------------------
Service dédié à l'optimisation des tournées de véhicules.
Utilise Google OR-Tools pour la résolution et OSRM pour les matrices de temps.

Fonctionnalités :
1. Calcul de matrice de temps (OSRM avec fallback Haversine).
2. Résolution du problème de tournées avec contraintes de capacité et de temps.
3. Gestion des pénalités pour les nœuds non visités.

Auteur : wi
"""

import os
import math
import requests
from typing import List, Tuple, Dict, Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from ortools.constraint_solver import pywrapcp
from ortools.constraint_solver import routing_enums_pb2

# --- CONFIGURATION ---

APP_TITLE = "Service VRP - Optimization Engine"
OSRM_URL = os.getenv("OSRM_URL", "http://osrm-backend:5000")

# Paramètres OSRM
OSRM_CHUNK_SIZE = 50  # Nombre de points par requête (évite les URLs trop longues)
OSRM_TIMEOUT = 2.0  # Secondes

# Paramètres OR-Tools
MAX_ROUTE_TIME = 86400  # 24 heures (secondes)
WAITING_TIME_SLACK = 3600  # Temps d'attente autorisé (secondes)
TIME_PENALTY_COST = 100  # Coût par seconde
UNVISITED_PENALTY = 1_000_000  # Coût très élevé pour forcer la visite
SOLVER_TIME_LIMIT = 30  # Temps max de recherche (secondes)

# Paramètres Fallback (Haversine)
FALLBACK_SPEED_KMH = 30.0


# --- MODÈLES DE DONNÉES ---

class Bin(BaseModel):
    """Représente un point de collecte (poubelle)."""
    id: str
    lat: float
    lon: float
    weight: float


class Truck(BaseModel):
    """Représente un véhicule de la flotte."""
    id: str
    capacity: float


class VRPRequest(BaseModel):
    """Payload de la requête d'optimisation."""
    bins: List[Bin]
    trucks: List[Truck]
    depot: Tuple[float, float]  # [Lat, Lon]


app = FastAPI(title=APP_TITLE)


# --- OUTILS MATHÉMATIQUES & GEOGRAPHIQUES ---

def haversine_duration(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """
    Estime la durée de trajet (en secondes) à vol d'oiseau.
    Utilisé comme méthode de secours si OSRM échoue.
    """
    R = 6371000  # Rayon Terre (m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_meters = R * c

    # Estimation : Distance * 1.4 (détours ville) / Vitesse
    speed_ms = FALLBACK_SPEED_KMH / 3.6
    return int((dist_meters * 1.4) / speed_ms)


def compute_time_matrix(locations: List[Tuple[float, float]]) -> List[List[int]]:
    """
    Construit la matrice des temps de trajet entre tous les points.
    Tente d'abord via OSRM, sinon repli sur Haversine.

    Args:
        locations: Liste de tuples (lon, lat). Note: OSRM prend (lon, lat).

    Returns:
        Matrice carrée NxN des durées en secondes.
    """
    n = len(locations)
    matrix = [[0] * n for _ in range(n)]

    print(f"[VRP] Calcul matrice pour {n} points...", flush=True)

    # Découpage par lots pour ne pas saturer l'URL OSRM
    for i in range(0, n, OSRM_CHUNK_SIZE):
        for j in range(0, n, OSRM_CHUNK_SIZE):
            src_batch = locations[i: i + OSRM_CHUNK_SIZE]
            dst_batch = locations[j: j + OSRM_CHUNK_SIZE]

            # 1. Formatage Coordonnées (Arrondi pour cache OSRM)
            src_str = [f"{round(lon, 6)},{round(lat, 6)}" for lon, lat in src_batch]
            dst_str = [f"{round(lon, 6)},{round(lat, 6)}" for lon, lat in dst_batch]

            # 2. Construction URL OSRM
            # Optimisation: si src == dst, on envoie une seule liste
            if i == j:
                all_coords = src_str
                src_idx = list(range(len(src_batch)))
                dst_idx = src_idx
            else:
                all_coords = src_str + dst_str
                src_idx = list(range(len(src_batch)))
                dst_idx = list(range(len(src_batch), len(src_batch) + len(dst_batch)))

            coords_url = ";".join(all_coords)
            src_params = ";".join(map(str, src_idx))
            dst_params = ";".join(map(str, dst_idx))

            # Paramètre radiuses : force la recherche de route proche
            radiuses = ";".join(["1000"] * len(all_coords))

            url = (f"{OSRM_URL}/table/v1/driving/{coords_url}"
                   f"?sources={src_params}&destinations={dst_params}"
                   f"&annotations=duration&radiuses={radiuses}")

            # 3. Appel API avec Fallback
            try:
                response = requests.get(url, timeout=OSRM_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") != "Ok":
                        raise ValueError(f"OSRM Error: {data.get('code')}")

                    durations = data.get("durations", [])

                    # Remplissage de la sous-matrice
                    for r, row in enumerate(durations):
                        for c, val in enumerate(row):
                            # Si OSRM renvoie None (pas de route), fallback
                            if val is None:
                                s_lat, s_lon = src_batch[r][1], src_batch[r][0]
                                d_lat, d_lon = dst_batch[c][1], dst_batch[c][0]
                                matrix[i + r][j + c] = haversine_duration(s_lat, s_lon, d_lat, d_lon)
                            else:
                                matrix[i + r][j + c] = int(val)
                else:
                    raise ConnectionError(f"HTTP {response.status_code}")

            except Exception as e:
                # Mode Dégradé : Calcul mathématique local
                print(f"[VRP] ⚠️ Échec OSRM sur le batch ({i},{j}) -> Fallback Haversine. Erreur: {e}")
                for r_idx, (src_lon, src_lat) in enumerate(src_batch):
                    for c_idx, (dst_lon, dst_lat) in enumerate(dst_batch):
                        matrix[i + r_idx][j + c_idx] = haversine_duration(src_lat, src_lon, dst_lat, dst_lon)

    return matrix


# --- LOGIQUE DE RÉSOLUTION (OR-TOOLS) ---

def solve_vrp_optimization(request: VRPRequest, time_matrix: List[List[int]]) -> Dict[str, Any]:
    """
    Configure et exécute le solveur Google OR-Tools.
    """
    # A. Préparation des données
    num_locations = len(time_matrix)
    num_vehicles = len(request.trucks)
    depot_index = 0

    # Capacités
    truck_capacities = [int(t.capacity) for t in request.trucks]

    # Demandes (Poids des poubelles)
    # Note: Le dépôt (index 0) a une demande de 0
    demands = [0] + [int(b.weight) for b in request.bins]

    # B. Création du modèle
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    # C. Définition des Coûts (Distance/Temps)
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Dimension Temps
    routing.AddDimension(
        transit_callback_index,
        WAITING_TIME_SLACK,  # Slack
        MAX_ROUTE_TIME,  # Horizon max
        True,  # Start at zero
        "Time"
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    # On ajoute un coût proportionnel au temps pour minimiser la durée totale
    time_dimension.SetGlobalSpanCostCoefficient(TIME_PENALTY_COST)

    # D. Définition des Contraintes (Capacité)
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # Null capacity slack
        truck_capacities,
        True,
        "Capacity"
    )

    # E. Gestion des nœuds non visités (Disjonctions)
    # Permet au solveur de laisser tomber une poubelle si impossible, mais avec forte pénalité
    for node in range(1, num_locations):
        routing.AddDisjunction([manager.NodeToIndex(node)], UNVISITED_PENALTY)

    # F. Paramètres de recherche
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    # Stratégie initiale rapide
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    # Optimisation locale guidée (Metaheuristic)
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = SOLVER_TIME_LIMIT

    # G. Résolution
    solution = routing.SolveWithParameters(search_parameters)

    # H. Extraction des résultats
    if solution:
        return extract_solution(request, manager, routing, solution)

    return {"status": "failed", "message": "Aucune solution trouvée par le solveur."}


def extract_solution(request: VRPRequest, manager, routing, solution) -> Dict[str, Any]:
    """Transforme la solution interne OR-Tools en JSON lisible."""
    routes = []
    total_time = 0
    dropped_count = 0

    # Identifier les points non visités
    for node in range(routing.Size()):
        if routing.IsStart(node) or routing.IsEnd(node):
            continue
        if solution.Value(routing.NextVar(node)) == node:
            dropped_count += 1

    # Construire les itinéraires par camion
    for vehicle_id in range(len(request.trucks)):
        index = routing.Start(vehicle_id)

        # Si le camion ne bouge pas (Start -> End direct), on ignore
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue

        route_points = []
        route_load = 0
        route_time = 0
        truck_obj = request.trucks[vehicle_id]

        while not routing.IsEnd(index):
            node_index = manager.IndexToNode(index)

            # Identification du point
            if node_index == 0:
                point_id = "DEPOT"
                lat, lon = request.depot
            else:
                # -1 car bins est indexé à partir de 0, mais node 0 est le dépôt
                bin_obj = request.bins[node_index - 1]
                point_id = bin_obj.id
                lat, lon = bin_obj.lat, bin_obj.lon
                route_load += bin_obj.weight

            previous_index = index
            index = solution.Value(routing.NextVar(index))

            # Temps de trajet pour cet arc
            arc_cost = routing.GetArcCostForVehicle(previous_index, index, vehicle_id)
            route_time += arc_cost

            route_points.append({
                "point_id": point_id,
                "lat": lat,
                "lon": lon,
                "load_after_visit": route_load
            })

        # Ajout du retour au dépôt
        route_points.append({
            "point_id": "DEPOT_RETURN",
            "lat": request.depot[0],
            "lon": request.depot[1],
            "final_load": route_load
        })

        routes.append({
            "truck_id": truck_obj.id,
            "total_time_seconds": route_time,
            "total_load": route_load,
            "stops": route_points
        })
        total_time += route_time

    return {
        "status": "optimized",
        "total_fleet_time": total_time,
        "dropped_bins": dropped_count,
        "routes": routes
    }


# --- API ENDPOINTS ---

@app.post("/solve_vrp")
def solve_vrp_endpoint(request: VRPRequest):
    """
    Endpoint principal.
    1. Reçoit la liste des poubelles et camions.
    2. Calcule la matrice de temps (OSRM).
    3. Résout le problème (OR-Tools).
    """
    print(f"[VRP] Nouvelle requête : {len(request.bins)} poubelles, {len(request.trucks)} camions.")

    if not request.bins or not request.trucks:
        return {"status": "error", "message": "Liste vide (poubelles ou camions)."}

    # 1. Préparation liste coordonnées : [Dépôt, Poubelle 1, Poubelle 2, ...]
    # Attention: request.depot est [Lat, Lon], OSRM veut [Lon, Lat]
    all_coordinates = [(request.depot[1], request.depot[0])]
    for b in request.bins:
        all_coordinates.append((b.lon, b.lat))

    # 2. Calcul Matrice
    time_matrix = compute_time_matrix(all_coordinates)

    # 3. Optimisation
    result = solve_vrp_optimization(request, time_matrix)

    return result