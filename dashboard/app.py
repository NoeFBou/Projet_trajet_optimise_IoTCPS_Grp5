import streamlit as st
import pymongo
import folium
from streamlit_folium import st_folium
import requests
import random

# Configuration
MONGO_URI = "mongodb://mongodb:27017/"
OSRM_URL = "http://osrm-backend:5000"

st.set_page_config(page_title="Optimisation Déchets Nice", layout="wide")
st.title("🚛 Suivi des Tournées de Collecte (Nice)")


# Connexion DB
@st.cache_resource
def get_db():
    client = pymongo.MongoClient(MONGO_URI)
    return client["waste_management"]


db = get_db()
col = db["routes_history"]

# Récupérer la dernière simulation
last_route = col.find_one(sort=[("timestamp", -1)])

if not last_route:
    st.warning("Aucune route trouvée en base. Attendez que l'Aggregator lance une optimisation.")
else:
    st.success(f"Dernière optimisation : {last_route['timestamp']}")

    # Création de la map centrée sur Nice
    m = folium.Map(location=[43.7102, 7.2620], zoom_start=13)

    routes = last_route.get("routes", [])

    colors = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue"]

    for i, truck_route in enumerate(routes):
        truck_id = truck_route.get("truck_id", f"Truck-{i}")
        stops = truck_route.get("stops", [])
        color = colors[i % len(colors)]

        # 1. Préparer les points pour OSRM (Lon,Lat)
        # OSRM Route service a besoin des points de passage pour dessiner la route exacte
        waypoints = []
        for stop in stops:
            waypoints.append(f"{stop['lon']},{stop['lat']}")

            # Marqueur pour la poubelle
            icon_color = "black" if stop['point_id'] == "DEPOT" else color
            icon_type = "home" if stop['point_id'] == "DEPOT" else "trash"

            folium.Marker(
                location=[stop['lat'], stop['lon']],
                popup=f"<b>{stop['point_id']}</b><br>Charge: {stop.get('load_after_visit', 0):.1f}kg",
                icon=folium.Icon(color=icon_color, icon=icon_type, prefix='fa')
            ).add_to(m)

        # 2. Appel à OSRM pour avoir la géométrie (le tracé bleu)
        if len(waypoints) > 1:
            coords_str = ";".join(waypoints)
            # overview=full demande la géométrie précise
            url = f"{OSRM_URL}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

            try:
                resp = requests.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    route_geo = data['routes'][0]['geometry']

                    # Dessiner la ligne
                    folium.GeoJson(
                        route_geo,
                        name=f"Trajet {truck_id}",
                        style_function=lambda x, col=color: {'color': col, 'weight': 4, 'opacity': 0.7}
                    ).add_to(m)
            except Exception as e:
                st.error(f"Erreur affichage route OSRM: {e}")

    # Affichage stats
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Camions utilisés", len(routes))
    with col2:
        st.metric("Temps total estimé", f"{last_route.get('total_fleet_time', 0) / 60:.0f} min")

    # Affichage Map
    st_folium(m, width=1200, height=600)