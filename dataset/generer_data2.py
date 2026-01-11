import geopandas as gpd
import pandas as pd
import numpy as np
import osmnx as ox
import json
import random
import requests
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# --- CONFIGURATION ---
FICHIER_BD_TOPO = "BDT_3-5_GPKG_LAMB93_D006-ED2025-09-15.gpkg"
CODE_INSEE_NICE = "06088"

print("1. Chargement de la géométrie de Nice...")
try:
    gdf_communes = gpd.read_file(FICHIER_BD_TOPO, layer="COMMUNE")
    if "code_insee" not in gdf_communes.columns:
        raise ValueError("Colonne 'code_insee' introuvable.")
    poly_nice = gdf_communes[gdf_communes["code_insee"] == CODE_INSEE_NICE].geometry.iloc[0]
    print("   > Géométrie de Nice récupérée.")
except Exception as e:
    print(f"ERREUR CRITIQUE : {e}")
    exit()

print("2. Chargement des bâtiments (Filtre spatial)...")
gdf_nice = gpd.read_file(FICHIER_BD_TOPO, layer="BATIMENT", mask=poly_nice)
print(f"   > Bâtiments chargés à Nice : {len(gdf_nice)}")

# --- 3. NETTOYAGE ET CALCULS ---
if "hauteur" in gdf_nice.columns:
    gdf_nice["hauteur"] = gdf_nice["hauteur"].fillna(6.0)
else:
    gdf_nice["hauteur"] = 6.0

gdf_nice["nb_etages_estime"] = np.where(
    gdf_nice["nombre_d_etages"] > 0,
    gdf_nice["nombre_d_etages"],
    (gdf_nice["hauteur"] / 3).astype(int)
)
gdf_nice["nb_etages_estime"] = gdf_nice["nb_etages_estime"].clip(lower=1)

# Calcul des surfaces
gdf_nice["surface_sol"] = gdf_nice.geometry.area
gdf_nice["surface_totale"] = gdf_nice["surface_sol"] * gdf_nice["nb_etages_estime"]

# --- 4. DATA ENRICHMENT (OSM - RESTOS) ---
print("3. Récupération des restaurants via OpenStreetMap...")
tags = {"amenity": ["restaurant", "fast_food", "cafe", "pub"]}
try:
    pois = ox.features_from_place("Nice, France", tags=tags)
    pois = pois.to_crs(gdf_nice.crs)
    pois = pois[pois.geometry.type == 'Point']

    print("   > Croisement spatial...")
    join_result = gpd.sjoin(gdf_nice, pois, how="left", predicate="contains")
    comptage_restos = join_result.groupby(join_result.index)["amenity"].count()
    gdf_nice["nb_restos"] = comptage_restos.fillna(0)
    print(f"   > {len(gdf_nice[gdf_nice['nb_restos'] > 0])} restos trouvés.")

except Exception as e:
    print(f"   ! Erreur OSM ({e}), on continue sans.")
    gdf_nice["nb_restos"] = 0


# --- 5. CALCUL VOLUME DÉCHETS ---
def estimer_dechets(row):
    usage = str(row.get("usage_1", "Indetermine"))
    surface = row["surface_totale"]
    nb_restos = row.get("nb_restos", 0)

    facteur_residentiel = (1 / 30) * 50
    facteur_commercial = facteur_residentiel * 3

    if "Commercial" in usage or "Industriel" in usage:
        volume = surface * facteur_commercial
    else:
        volume = surface * facteur_residentiel

    if nb_restos > 0:
        volume += (1000 * nb_restos)

    random_factor = np.random.uniform(0.8, 1.2)
    return int(volume * random_factor)


print("4. Calcul des déchets...")
gdf_nice["volume_dechet_L_semaine"] = gdf_nice.apply(estimer_dechets, axis=1)
gdf_nice["nb_bacs_660L"] = np.ceil(gdf_nice["volume_dechet_L_semaine"] / 660).astype(int)

# --- 6. CONVERSION VERS WGS84 ---
print("5. Conversion des coordonnées en GPS (WGS84)...")
gdf_nice = gdf_nice.to_crs(epsg=4326)

# --- 7. QUARTIERS ---
print("5b. Récupération des quartiers de Nice via OSM...")
try:
    tags_quartier = {"place": ["suburb", "quarter", "neighbourhood"]}
    gdf_quartiers = ox.features_from_place("Nice, France", tags=tags_quartier)
    gdf_quartiers = gdf_quartiers[gdf_quartiers.geometry.type.isin(['Polygon', 'MultiPolygon'])]

    gdf_quartiers = gdf_quartiers[["name", "geometry"]].to_crs(epsg=4326)
    gdf_quartiers = gdf_quartiers.rename(columns={"name": "nom_quartier"})

    print(f"   > {len(gdf_quartiers)} quartiers trouvés.")

    gdf_nice_centroids = gdf_nice.copy()
    gdf_nice_centroids.geometry = gdf_nice.geometry.centroid

    joined = gpd.sjoin(gdf_nice_centroids, gdf_quartiers, how="left", predicate="within")
    gdf_nice["zone_id"] = joined["nom_quartier"]
    gdf_nice["zone_id"] = gdf_nice["zone_id"].fillna("Zone_Non_Definie")

except Exception as e:
    print(f"   ! Impossible de récupérer les quartiers ({e}). On mettra 'Zone_Defaut'.")
    gdf_nice["zone_id"] = "Zone_Defaut"

# --- 8. EXPORT (DÉPLACÉ ICI POUR AVOIR LES QUARTIERS) ---
output_geojson = "simulation_dechets_nice.geojson"
output_csv = "simulation_dechets_nice.csv"

cols_a_garder = [
    "cleabs", "usage_1", "hauteur", "nb_etages_estime",
    "volume_dechet_L_semaine", "nb_bacs_660L", "geometry", "zone_id"  # Ajout zone_id
]
cols_finales = [c for c in cols_a_garder if c in gdf_nice.columns]

print("6. Sauvegarde des fichiers statiques...")
gdf_nice[cols_finales].to_file(output_geojson, driver="GeoJSON")

df_csv = gdf_nice[cols_finales].copy()
df_csv["latitude"] = df_csv.geometry.centroid.y
df_csv["longitude"] = df_csv.geometry.centroid.x
df_csv.drop(columns="geometry").to_csv(output_csv, index=False)
print("   > Fichiers générés avec succès.")


# --- 9. SIMULATION IOT AVEC SNAPPING OSRM ---
def get_snapped_coordinate(lat, lon):
    try:
        url = f"http://localhost:5000/nearest/v1/driving/{lon},{lat}?number=1"
        response = requests.get(url, timeout=0.5)
        if response.status_code == 200:
            data = response.json()
            if data['code'] == 'Ok':
                snapped = data['waypoints'][0]['location']
                return snapped[1], snapped[0]
    except:
        pass
    return lat, lon


TAILLE_BAC_INDIVIDUEL = 240
TAILLE_BAC_COMMERCE = 660

#pourcentage de batiment qu on garde 0.4 = 40%
PROBABILITE_CONSERVATION = 0.01

PROPS_DECHETS = {
    "Verre": {"density": 0.35, "part_volume": 0.15},
    "Recyclable": {"density": 0.05, "part_volume": 0.40},
    "Organique": {"density": 0.50, "part_volume": 0.30},
    "TousDechets": {"density": 0.15, "part_volume": 0.60}
}

output_json_iot = "simulation_iot_poubelles_light.json"
liste_capteurs = []

print(f"7. Génération IoT avec Snapping OSRM (Cible : ~{int(len(gdf_nice) * PROBABILITE_CONSERVATION)} bâtiments)...")

try:
    from tqdm import tqdm

    iterator = tqdm(gdf_nice.iterrows(), total=len(gdf_nice))
except ImportError:
    iterator = gdf_nice.iterrows()

for idx, row in iterator:

    has_resto = row.get("nb_restos", 0) > 0
    volume_jour = row["volume_dechet_L_semaine"] / 7.0

    if not has_resto and random.random() > PROBABILITE_CONSERVATION:
        continue
    if volume_jour < 5:
        continue

    # Coordonnées brutes (WGS84)
    raw_lat = row.geometry.centroid.y
    raw_lon = row.geometry.centroid.x

    lat, lon = get_snapped_coordinate(raw_lat, raw_lon)
    lat = round(lat, 6)
    lon = round(lon, 6)

    bat_id = str(row.get("cleabs", f"BAT-{idx}"))[-6:]
    quartier_actuel = row.get("zone_id", "Inconnu")

    if has_resto or volume_jour > 80:
        capacite_bac = TAILLE_BAC_COMMERCE
    else:
        capacite_bac = TAILLE_BAC_INDIVIDUEL

    if has_resto or random.random() < 0.3:
        types_bacs = ["Verre", "Recyclable", "Organique"]
    else:
        types_bacs = ["TousDechets", "Recyclable"]

    for type_dechet in types_bacs:
        props = PROPS_DECHETS[type_dechet]
        random_noise = random.uniform(0.8, 1.2)
        daily_growth = int(volume_jour * props["part_volume"] * random_noise)
        if daily_growth < 1: daily_growth = 1

        if daily_growth > capacite_bac: capacite_bac = 660

        current_level = int(capacite_bac * random.uniform(0.0, 0.75))

        capteur = {
            "id": f"PBL-{bat_id}-{type_dechet[:3].upper()}",
            "type": type_dechet,
            "zone": quartier_actuel,
            "daily_growth": daily_growth,
            "current_level": current_level,
            "max_capacity": capacite_bac,
            "density": props["density"],
            "dims": "STANDARD_DIMS",
            "coords": {"lat": lat, "lon": lon}
        }
        liste_capteurs.append(capteur)

# --- SAUVEGARDE ---
print(f"Génération terminée : {len(liste_capteurs)} poubelles générées.")
print(f"Sauvegarde dans {output_json_iot}...")
with open(output_json_iot, "w", encoding="utf-8") as f:
    json.dump(liste_capteurs, f, ensure_ascii=False, indent=2)

print("Terminé !")