import geopandas as gpd
import pandas as pd
import numpy as np
import osmnx as ox
import json
import random
# --- CONFIGURATION ---
# Assurez-vous que le nom du fichier est correct
FICHIER_BD_TOPO = "BDT_3-5_GPKG_LAMB93_D006-ED2025-09-15.gpkg"
CODE_INSEE_NICE = "06088"

print("1. Chargement de la géométrie de Nice...")

try:
    # On charge la couche COMMUNE
    gdf_communes = gpd.read_file(FICHIER_BD_TOPO, layer="COMMUNE")

    if "code_insee" not in gdf_communes.columns:
        raise ValueError("La colonne 'code_insee' est introuvable dans la couche COMMUNE.")

    # On récupère le polygone de Nice
    poly_nice = gdf_communes[gdf_communes["code_insee"] == CODE_INSEE_NICE].geometry.iloc[0]
    print("   > Géométrie de Nice récupérée avec succès.")

except Exception as e:
    print(f"ERREUR CRITIQUE lors de la lecture des communes : {e}")
    exit()

print("2. Chargement des bâtiments (Filtre spatial)...")
# On utilise mask=poly_nice pour ne charger que ce qui est DANS Nice (très rapide)
gdf_nice = gpd.read_file(FICHIER_BD_TOPO, layer="BATIMENT", mask=poly_nice)

print(f"   > Bâtiments chargés à Nice : {len(gdf_nice)}")

# --- 3. NETTOYAGE ET CALCULS ---
# Adaptation aux noms de colonnes minuscules de votre fichier

# Remplissage hauteur manquante (par défaut 6m)
if "hauteur" in gdf_nice.columns:
    gdf_nice["hauteur"] = gdf_nice["hauteur"].fillna(6.0)
else:
    gdf_nice["hauteur"] = 6.0

# Calcul du nombre d'étages
# On utilise "nombre_d_etages" (minuscule)
gdf_nice["nb_etages_estime"] = np.where(
    gdf_nice["nombre_d_etages"] > 0,
    gdf_nice["nombre_d_etages"],
    (gdf_nice["hauteur"] / 3).astype(int)
)
gdf_nice["nb_etages_estime"] = gdf_nice["nb_etages_estime"].clip(lower=1)

# Calcul des surfaces
gdf_nice["surface_sol"] = gdf_nice.geometry.area
gdf_nice["surface_totale"] = gdf_nice["surface_sol"] * gdf_nice["nb_etages_estime"]

# --- 4. DATA ENRICHMENT (OSM) ---

print("3. Récupération des restaurants via OpenStreetMap...")
tags = {"amenity": ["restaurant", "fast_food", "cafe", "pub"]}
try:
    pois = ox.features_from_place("Nice, France", tags=tags)

    # Vérification et conversion CRS
    pois = pois.to_crs(gdf_nice.crs)

    # On ne garde que les points
    pois = pois[pois.geometry.type == 'Point']

    print("   > Croisement spatial...")
    join_result = gpd.sjoin(gdf_nice, pois, how="left", predicate="contains")
    comptage_restos = join_result.groupby(join_result.index)["amenity"].count()
    gdf_nice["nb_restos"] = comptage_restos.fillna(0)

    print(f"   > {len(gdf_nice[gdf_nice['nb_restos'] > 0])} bâtiments identifiés avec des restaurants.")

except Exception as e:
    print(f"   ! Attention : Erreur OSM ou pas de connexion ({e}). On continue sans les restos.")
    gdf_nice["nb_restos"] = 0


# --- 5. SIMULATION ---

def estimer_dechets(row):
    # CORRECTION : On utilise "usage_1" en minuscule
    usage = str(row.get("usage_1", "Indetermine"))
    surface = row["surface_totale"]
    nb_restos = row.get("nb_restos", 0)

    # Base calcul (Litre / semaine)
    facteur_residentiel = (1 / 30) * 50  # ~1.6L par m2
    facteur_commercial = facteur_residentiel * 3

    if "Commercial" in usage or "Industriel" in usage:
        volume = surface * facteur_commercial
    else:
        volume = surface * facteur_residentiel

    # Bonus Resto
    if nb_restos > 0:
        volume += (1000 * nb_restos)

    random_factor = np.random.uniform(0.8, 1.2)
    return int(volume * random_factor)


print("4. Calcul des déchets...")
gdf_nice["volume_dechet_L_semaine"] = gdf_nice.apply(estimer_dechets, axis=1)
gdf_nice["nb_bacs_660L"] = np.ceil(gdf_nice["volume_dechet_L_semaine"] / 660).astype(int)

# --- 6. EXPORT ---
output_geojson = "simulation_dechets_nice.geojson"
output_csv = "simulation_dechets_nice.csv"

# Sélection des colonnes finales (noms minuscules corrects)
cols_a_garder = [
    "cleabs", "usage_1", "hauteur", "nb_etages_estime",
    "volume_dechet_L_semaine", "nb_bacs_660L", "geometry"
]
# Sécurité : on ne garde que les colonnes qui existent vraiment
cols_finales = [c for c in cols_a_garder if c in gdf_nice.columns]

# --- 5b. AJOUT DES QUARTIERS (VIA OSM) ---
print("5b. Récupération des quartiers de Nice via OSM...")

try:
    # On télécharge les zones définies comme 'suburb' (quartier) ou 'neighbourhood'
    tags_quartier = {"place": ["suburb", "quarter", "neighbourhood"]}
    gdf_quartiers = ox.features_from_place("Nice, France", tags=tags_quartier)

    # On ne garde que les polygones (zones) et on simplifie les colonnes
    gdf_quartiers = gdf_quartiers[gdf_quartiers.geometry.type.isin(['Polygon', 'MultiPolygon'])]
    gdf_quartiers = gdf_quartiers[["name", "geometry"]].to_crs(gdf_nice.crs)

    # Renommer la colonne pour éviter les conflits
    gdf_quartiers = gdf_quartiers.rename(columns={"name": "nom_quartier"})

    print(f"   > {len(gdf_quartiers)} quartiers trouvés (ex: {gdf_quartiers['nom_quartier'].iloc[0]}).")
    print("   > Attribution des quartiers aux bâtiments...")

    # JOINTURE SPATIALE
    # On regarde dans quel quartier tombe le CENTRE de chaque bâtiment
    # Cela évite les bugs si un bâtiment est à cheval sur deux zones
    gdf_nice_centroids = gdf_nice.copy()
    gdf_nice_centroids.geometry = gdf_nice.geometry.centroid

    # op="within" signifie : le point est DANS le polygone
    joined = gpd.sjoin(gdf_nice_centroids, gdf_quartiers, how="left", predicate="within")

    # On réinjecte le nom du quartier dans le DataFrame principal
    gdf_nice["zone_id"] = joined["nom_quartier"]

    # Remplir les vides (bâtiments hors zones définies) par "Centre_Ville" ou "Autre"
    gdf_nice["zone_id"] = gdf_nice["zone_id"].fillna("Zone_Non_Definie")

except Exception as e:
    print(f"   ! Impossible de récupérer les quartiers ({e}). On mettra 'Zone_Defaut'.")
    gdf_nice["zone_id"] = "Zone_Defaut"


print("5. Sauvegarde des fichiers...")
gdf_nice[cols_finales].to_file(output_geojson, driver="GeoJSON")

# Pour le CSV
df_csv = gdf_nice[cols_finales].copy()
df_csv["latitude"] = df_csv.geometry.centroid.y
df_csv["longitude"] = df_csv.geometry.centroid.x
df_csv.drop(columns="geometry").to_csv(output_csv, index=False)

print("Terminé ! Fichiers générés avec succès.")

# --- CONFIGURATION ---
TAILLE_BAC_INDIVIDUEL = 240  # 240L (standard maison)
TAILLE_BAC_COMMERCE = 660  # 660L (gros bac 4 roues pour restos/gros immeubles)

# Pourcentage de bâtiments qu'on GARDE (0.4 = on garde 40%, on jette 60%)
PROBABILITE_CONSERVATION = 0.00001

PROPS_DECHETS = {
    "Verre": {"density": 0.35, "part_volume": 0.15},
    "Recyclable": {"density": 0.05, "part_volume": 0.40},
    "Organique": {"density": 0.50, "part_volume": 0.30},
    "TousDechets": {"density": 0.15, "part_volume": 0.60}
}

output_json_iot = "simulation_iot_poubelles_light.json"
liste_capteurs = []

print(f"6. Génération allégée des capteurs (Cible : ~{int(len(gdf_nice) * PROBABILITE_CONSERVATION)} bâtiments)...")

# Barre de progression
try:
    from tqdm import tqdm

    iterator = tqdm(gdf_nice.iterrows(), total=len(gdf_nice))
except ImportError:
    iterator = gdf_nice.iterrows()

for idx, row in iterator:

    # --- 1. FILTRAGE DRASTIQUE ---
    has_resto = row.get("nb_restos", 0) > 0
    volume_jour = row["volume_dechet_L_semaine"] / 7.0

    # LOGIQUE DE SUPPRESSION :
    # Si ce n'est PAS un resto ET que le tirage aléatoire est au-dessus du seuil -> On supprime
    if not has_resto and random.random() > PROBABILITE_CONSERVATION:
        continue

    # On supprime aussi les volumes ridicules (< 5L / jour)
    if volume_jour < 5:
        continue

    # --- 2. PARAMÈTRES DU BÂTIMENT ---
    bat_id = str(row.get("cleabs", f"BAT-{idx}"))[-6:]
    lat = round(row.geometry.centroid.y, 5)
    lon = round(row.geometry.centroid.x, 5)

    # Définition de la capacité du bac pour ce bâtiment
    # Si gros volume ou resto -> Gros bac (660L), sinon bac individuel (240L)
    if has_resto or volume_jour > 80:
        capacite_bac = TAILLE_BAC_COMMERCE
    else:
        capacite_bac = TAILLE_BAC_INDIVIDUEL

    # --- 3. SCÉNARIOS DE TRI ---
    # Restos ou 30% des gens -> Tri complet
    if has_resto or random.random() < 0.3:
        types_bacs = ["Verre", "Recyclable", "Organique"]
    else:
        types_bacs = ["TousDechets", "Recyclable"]

    # --- 4. CRÉATION DES CAPTEURS ---
    for type_dechet in types_bacs:
        props = PROPS_DECHETS[type_dechet]

        part = props["part_volume"]
        if "TousDechets" in types_bacs and type_dechet == "Recyclable":
            part = 0.4

            # Calcul croissance
        random_noise = random.uniform(0.8, 1.2)
        daily_growth = int(volume_jour * part * random_noise)
        if daily_growth < 1: daily_growth = 1


        if daily_growth > capacite_bac:
            capacite_bac = 660

        start_percentage = random.uniform(0.0, 0.75)
        current_level = int(capacite_bac * start_percentage)

        quartier_actuel = row.get("zone_id", "Inconnu")

        capteur = {
            "id": f"PBL-{bat_id}-{type_dechet[:3].upper()}",
            "type": type_dechet,
            "zone": quartier_actuel,
            "daily_growth": daily_growth,
            "current_level": current_level,
            "max_capacity": capacite_bac,
            "density": props["density"],
            "dims": "STANDARD_DIMS",
            "coords": {
                "lat": lat,
                "lon": lon
            }
        }
        liste_capteurs.append(capteur)

# --- SAUVEGARDE ---
print(f"Génération terminée : {len(liste_capteurs)} poubelles générées.")
print(f"Sauvegarde dans {output_json_iot}...")

with open(output_json_iot, "w", encoding="utf-8") as f:
    json.dump(liste_capteurs, f, ensure_ascii=False, indent=2)

print("Terminé !")