import geopandas as gpd

FICHIER = "BDT_3-5_GPKG_LAMB93_D006-ED2025-09-15.gpkg"

print("--- Inspection de la couche BATIMENT ---")
# On lit seulement 5 lignes pour aller vite
gdf_test = gpd.read_file(FICHIER, layer="BATIMENT", rows=5)
print("Colonnes disponibles dans BATIMENT :")
print(gdf_test.columns.tolist())

print("\n--- Inspection de la couche COMMUNE ---")
# On regarde aussi la couche COMMUNE, on en aura besoin pour l'optimisation
gdf_commune = gpd.read_file(FICHIER, layer="COMMUNE", rows=5)
print("Colonnes disponibles dans COMMUNE :")
print(gdf_commune.columns.tolist())