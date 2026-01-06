import streamlit as st
import pymongo
import folium
from streamlit_folium import st_folium
import requests
import random
from datetime import datetime

# Configuration
MONGO_URI = "mongodb://mongodb:27017/"
OSRM_URL = "http://osrm-backend:5000"

st.set_page_config(page_title="Optimisation Déchets Nice", layout="wide")


# --- FONCTIONS ---

@st.cache_resource
def get_db():
    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.server_info()
        return client["waste_management"]
    except Exception as e:
        st.error(f"Impossible de se connecter à MongoDB : {e}")
        return None


def get_route_geometry(waypoints):
    """Interroge OSRM pour obtenir la géométrie précise"""
    if len(waypoints) < 2:
        return None

    # On arrondit pour éviter les URLs trop longues
    waypoints_clean = []
    for wp in waypoints:
        try:
            lon, lat = wp.split(',')
            waypoints_clean.append(f"{float(lon):.6f},{float(lat):.6f}")
        except:
            continue

    if not waypoints_clean: return None

    coords_str = ";".join(waypoints_clean)
    url = f"{OSRM_URL}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

    try:
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            return data['routes'][0]['geometry']
    except Exception:
        return None
    return None


# --- UI PRINCIPALE ---

st.title("🚛 Suivi des Tournées de Collecte (Nice)")

db = get_db()

if db is not None:
    col = db["routes_history"]

    # 1. RÉCUPÉRATION HISTORIQUE
    history_cursor = col.find({}, {"timestamp": 1}).sort("timestamp", -1)
    history_list = list(history_cursor)

    if not history_list:
        st.warning("⚠️ Aucune donnée de route trouvée en base. Attendez que l'Aggregator lance une optimisation.")
    else:
        # --- BARRE LATÉRALE : SÉLECTION SIMULATION ---
        st.sidebar.header("📅 Historique")

        options_map = {
            doc["timestamp"].strftime("%d/%m/%Y à %H:%M:%S"): doc["_id"]
            for doc in history_list
        }

        selected_label = st.sidebar.selectbox(
            "Choisir une optimisation :",
            options=list(options_map.keys()),
            index=0
        )

        selected_id = options_map[selected_label]
        simulation_data = col.find_one({"_id": selected_id})

        # --- BARRE LATÉRALE : CASES À COCHER CAMIONS ---
        st.sidebar.markdown("---")
        st.sidebar.header("🚚 Flotte de camions")

        routes = simulation_data.get("routes", [])

        # --- CORRECTION ICI : DÉDOUBLONNAGE DES IDS ---
        # On récupère tous les IDs bruts
        raw_truck_ids = [r.get("truck_id", f"Truck-{i}") for i, r in enumerate(routes)]
        # On crée une liste unique et triée pour la sidebar
        unique_truck_ids = sorted(list(set(raw_truck_ids)))
        # ----------------------------------------------

        # -- Gestion Sélection / Désélection massive --
        col_btn1, col_btn2 = st.sidebar.columns(2)
        if col_btn1.button("Tout cocher"):
            for tid in unique_truck_ids:
                st.session_state[f"chk_{tid}"] = True

        if col_btn2.button("Tout décocher"):
            for tid in unique_truck_ids:
                st.session_state[f"chk_{tid}"] = False

        st.sidebar.write(f"*{len(unique_truck_ids)} camions déployés*")

        selected_trucks = []

        # -- Boucle de création des Checkbox (Sur la liste UNIQUE) --
        container = st.sidebar.container()

        with container:
            for truck_id in unique_truck_ids:
                # On initialise la clé dans session_state si elle n'existe pas
                if f"chk_{truck_id}" not in st.session_state:
                    st.session_state[f"chk_{truck_id}"] = True

                # Création de la checkbox
                # La clé est unique car truck_id vient de unique_truck_ids
                is_checked = st.checkbox(
                    f"Camion {truck_id}",
                    key=f"chk_{truck_id}"
                )

                if is_checked:
                    selected_trucks.append(truck_id)

        # --- AFFICHAGE PRINCIPAL ---

        # On filtre les routes si leur ID est dans la liste sélectionnée
        routes_to_display = [r for r in routes if r.get("truck_id") in selected_trucks]

        if not routes_to_display:
            st.info("Aucun camion sélectionné. Cochez des cases dans la barre latérale.")
        else:
            # Map centrée sur Nice
            m = folium.Map(location=[43.7102, 7.2620], zoom_start=13)

            colors_palette = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue", "darkgreen",
                              "darkblue", "black"]
            total_time_filtered = 0

            progress_bar = st.progress(0)

            for i, truck_route in enumerate(routes_to_display):
                truck_id = truck_route.get("truck_id", "Unknown")
                stops = truck_route.get("stops", [])
                total_time_filtered += truck_route.get("total_time_seconds", 0)

                # Couleur constante basée sur le nom
                color_idx = abs(hash(truck_id)) % len(colors_palette)
                color = colors_palette[color_idx]

                waypoints = []
                for stop in stops:
                    waypoints.append(f"{stop['lon']},{stop['lat']}")

                    is_depot = "DEPOT" in stop['point_id']
                    icon_color = "black" if is_depot else color
                    icon_icon = "home" if is_depot else "trash"

                    # Popup un peu plus riche
                    charge = stop.get('load_after_visit', 0)
                    popup_html = f"<b>{stop['point_id']}</b><br>Camion: {truck_id}<br>Charge: {charge:.1f}kg"

                    folium.Marker(
                        location=[stop['lat'], stop['lon']],
                        popup=folium.Popup(popup_html, max_width=200),
                        icon=folium.Icon(color=icon_color, icon=icon_icon, prefix='fa')
                    ).add_to(m)

                geo_data = get_route_geometry(waypoints)
                if geo_data:
                    folium.GeoJson(
                        geo_data,
                        name=f"Trajet {truck_id}",
                        style_function=lambda x, col=color: {'color': col, 'weight': 4, 'opacity': 0.8},
                        tooltip=f"Trajet {truck_id}"
                    ).add_to(m)

                progress_bar.progress((i + 1) / len(routes_to_display))

            progress_bar.empty()

            # Stats
            st.markdown("### 📊 Statistiques")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Simulation", selected_label)
            with col2:
                st.metric("Trajets affichés", f"{len(routes_to_display)}")
            with col3:
                st.metric("Temps cumulé", f"{int(total_time_filtered / 60)} min")

            st_folium(m, width=1400, height=700)