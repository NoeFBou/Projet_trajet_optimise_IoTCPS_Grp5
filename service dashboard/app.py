import streamlit as st
import pymongo
import folium
from streamlit_folium import st_folium
import requests
import time
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


def get_bin_info_from_id(point_id):
    """Décode l'ID (ex: PBL-0123-VER) pour avoir des infos lisibles"""
    if "DEPOT" in point_id:
        return "Dépôt", "Centre logistique"

    parts = point_id.split('-')
    # On suppose le format PBL-{ID}-{TYPE}
    if len(parts) >= 3:
        code = parts[-1]
        mapping = {
            "VER": "Verre 🟢",
            "REC": "Recyclable 🟡",
            "ORG": "Organique 🟤",
            "TOU": "Tout-Venant ⚫"
        }
        return mapping.get(code, code), parts[1]
    return "Inconnu", "N/A"


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
        # --- BARRE LATÉRALE ---

        # 1. Bouton Action
        st.sidebar.title("🎮 Contrôle")
        if st.sidebar.button("🚀 GÉNÉRER TOURNEES", type="primary"):
            with st.spinner("Calcul des itinéraires en cours (VRP)..."):
                try:
                    response = requests.post("http://aggregator-service:5000/run-optimization", timeout=70)
                    if response.status_code == 200:
                        res_json = response.json()
                        if res_json['status'] == 'success':
                            st.sidebar.success(f"Succès ! {res_json['routes_count']} tournées.")
                            time.sleep(1)
                            st.rerun()
                        elif res_json['status'] == 'no_action':
                            st.sidebar.info("Rien à faire.")
                        else:
                            st.sidebar.error(f"Échec : {res_json.get('message')}")
                    else:
                        st.sidebar.error(f"Erreur HTTP : {response.status_code}")
                except Exception as e:
                    st.sidebar.error(f"Impossible de joindre l'Aggregator : {e}")

        st.sidebar.markdown("---")

        # 2. Historique
        st.sidebar.header("📅 Historique")
        options_map = {
            doc["timestamp"].strftime("%d/%m/%Y à %H:%M:%S"): doc["_id"]
            for doc in history_list
        }

        selected_label = st.sidebar.selectbox("Choisir une optimisation :", options=list(options_map.keys()), index=0)
        selected_id = options_map[selected_label]
        simulation_data = col.find_one({"_id": selected_id})

        # 3. Liste des trajets
        st.sidebar.markdown("---")
        st.sidebar.header("📍 Détail des tournées")

        routes = simulation_data.get("routes", [])

        col_btn1, col_btn2 = st.sidebar.columns(2)
        if col_btn1.button("Tout cocher"):
            for i in range(len(routes)): st.session_state[f"chk_route_{i}"] = True
        if col_btn2.button("Tout décocher"):
            for i in range(len(routes)): st.session_state[f"chk_route_{i}"] = False

        unique_trucks = len(set(r['truck_id'] for r in routes))
        st.sidebar.write(f"*{len(routes)} tournées pour {unique_trucks} camions*")

        selected_indices = []
        container = st.sidebar.container()
        with container:
            for i, route in enumerate(routes):
                tid = route.get("truck_id", "Inconnu")
                nb_stops = len(route.get("stops", [])) - 2
                load = route.get("total_load", 0)

                key = f"chk_route_{i}"
                if key not in st.session_state: st.session_state[key] = True

                label = f"#{i + 1} : {tid} ({nb_stops} arrêts - {int(load)}kg)"
                if st.checkbox(label, key=key):
                    selected_indices.append(i)

        # --- AFFICHAGE PRINCIPAL ---

        routes_to_display = [routes[i] for i in selected_indices]

        if not routes_to_display:
            st.info("Aucune tournée sélectionnée.")
        else:
            m = folium.Map(location=[43.7102, 7.2620], zoom_start=13)
            colors_palette = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue", "darkgreen",
                              "darkblue", "black"]

            # --- MODIFICATION: CALCUL DU TEMPS MAX ---
            max_time_filtered = 0  # On cherche le trajet le plus long (Makespan)

            progress_bar = st.progress(0)

            for idx_loop, truck_route in enumerate(routes_to_display):
                truck_id = truck_route.get("truck_id", "Unknown")
                stops = truck_route.get("stops", [])

                # Récupération du temps de ce trajet spécifique
                route_duration = truck_route.get("total_time_seconds", 0)
                if route_duration > max_time_filtered:
                    max_time_filtered = route_duration

                color = colors_palette[abs(hash(truck_id)) % len(colors_palette)]

                waypoints = []
                for stop in stops:
                    waypoints.append(f"{stop['lon']},{stop['lat']}")
                    is_depot = "DEPOT" in stop['point_id']

                    icon_color = "black" if is_depot else color
                    icon_icon = "home" if is_depot else "trash"

                    # --- MODIFICATION: POPUP HTML ENRICHIE ---
                    bin_type, bin_num = get_bin_info_from_id(stop['point_id'])
                    collected_weight = stop.get('load_after_visit', 0)

                    if is_depot:
                        popup_html = f"<b>🏢 DÉPÔT CENTRAL</b><br>Camion: {truck_id}"
                    else:
                        # On récupère le poids collecté à cet arrêt précis si possible
                        # Note: load_after_visit est cumulatif.
                        # Pour simplifier l'affichage ici on montre le cumul ou juste l'ID.
                        popup_html = f"""
                        <div style="font-family: sans-serif; min-width: 150px;">
                            <h5 style="margin:0;">🗑️ {bin_type}</h5>
                            <hr style="margin: 5px 0;">
                            <b>ID:</b> {stop['point_id']}<br>
                            <b>Camion:</b> {truck_id}<br>
                            <b>Poids cumulé:</b> {collected_weight:.1f} kg
                        </div>
                        """

                    folium.Marker(
                        location=[stop['lat'], stop['lon']],
                        popup=folium.Popup(popup_html, max_width=300),
                        icon=folium.Icon(color=icon_color, icon=icon_icon, prefix='fa')
                    ).add_to(m)

                geo_data = get_route_geometry(waypoints)
                if geo_data:
                    folium.GeoJson(
                        geo_data,
                        name=f"Trajet {truck_id}",
                        style_function=lambda x, col=color: {'color': col, 'weight': 4, 'opacity': 0.8},
                        tooltip=f"Trajet {truck_id} ({int(route_duration / 60)} min)"
                    ).add_to(m)

                progress_bar.progress((idx_loop + 1) / len(routes_to_display))

            progress_bar.empty()

            # Stats
            st.markdown("### 📊 Statistiques de la sélection")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Simulation", selected_label)
            with col2:
                st.metric("Tournées affichées", f"{len(routes_to_display)}")

            # --- AFFICHAGE DU TEMPS MAX ---
            hours = int(max_time_filtered // 3600)
            minutes = int((max_time_filtered % 3600) // 60)
            st.metric("Durée Opération (Fin au plus tard)", f"{hours}h {minutes}min")

            st_folium(m, width=1400, height=700)