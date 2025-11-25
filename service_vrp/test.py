import requests
import random
from fastapi import FastAPI, HTTPException






OSRM_URL = "http://localhost:5000/table/v1/driving/"
NB_POINTS = 50

CENTER_LAT = 43.7474461554596
CENTER_LON = 7.256310695041148


def generate_random_points(n, center_lat, center_lon):
    """Génère N points aléatoires autour d'un centre."""
    points = []
    for _ in range(n):
        # Dispersion d'environ 10-15km
        lat = center_lat + random.uniform(-0.1, 0.1)
        lon = center_lon + random.uniform(-0.15, 0.15)
        points.append((lon, lat))
    return points


def get_osrm_matrix(points_list):
    """Interroge le docker local OSRM pour obtenir la matrice de durée."""

    # Formatage de l'URL : lon1,lat1;lon2,lat2;...
    coordinates_str = ";".join([f"{lon},{lat}" for lon, lat in points_list])

    # --- OPTIMISATION ---
    # L'URL complète
    # annotations=duration : récupère le temps en secondes
    # skip_waypoints=true : ne renvoie pas les points snappés (allège la réponse)
    full_url = f"{OSRM_URL}{coordinates_str}?annotations=duration&skip_waypoints=true"

    try:
        print(f"Interrogation de OSRM ({len(points_list)} points)...")
        response = requests.get(full_url)

        # Si l'URL est trop longue (erreur 414), il faudra passer par le profil lua ou faire des paquets
        if response.status_code == 414:
            print("Erreur : L'URL est trop longue pour le serveur OSRM.")
            return None

        data = response.json()

        if data.get("code") != "Ok":
            print(f"Erreur OSRM : {data.get('message')}")
            return None

        # data["durations"] est une liste de listes (Matrice N x N)
        return data["durations"]

    except requests.exceptions.ConnectionError:
        print("Erreur : Impossible de se connecter à localhost:5001. Le Docker est-il lancé ?")
        return None
    except Exception as e:
        print(f"Autre erreur : {e}")
        return None


# --- Exécution ---

if __name__ == "__main__":
    # 1. Génération
    locations = generate_random_points(NB_POINTS, CENTER_LAT, CENTER_LON)

    # 2. Appel
    matrix = get_osrm_matrix(locations)

    if matrix:
        print("\n--- Matrice récupérée avec succès ! ---")
        print(f"Dimensions : {len(matrix)} x {len(matrix[0])}")

        print(f"Temps du point 0 vers le point 1 : {matrix[0][1]:.2f} secondes")
        print(f"Temps du point 0 vers le point 2 : {matrix[0][2]:.2f} secondes")
    else:
        print("Échec.")