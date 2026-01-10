"""
Dashboard de Supervision
------------------------------------
Interface utilisateur pour le suivi des tournées de collecte de déchets.

Fonctionnalités :
1. Visualisation cartographique des itinéraires (Folium).
2. Déclenchement manuel de l'optimisation (via Aggregator).
3. Consultation de l'historique des tournées.
4. Statistiques opérationnelles (Temps de flotte, charge).

Auteur : wi
"""

import time
import requests
import folium
import pymongo
from datetime import datetime
from zoneinfo import ZoneInfo  # Nécessite Python 3.9+ (Standard)
from typing import Optional, List, Dict, Tuple, Any

import streamlit as st
from streamlit_folium import st_folium

# --- CONFIGURATION & CONSTANTES ---

# Paramètres de l'application
PAGE_TITLE = "🚛 Suivi des Tournées de Collecte (Nice)"
LAYOUT = "wide"
NICE_COORDS = [43.7102, 7.2620]

# Timezone
PARIS_TZ = ZoneInfo("Europe/Paris")

# Connexions Services
MONGO_URI = "mongodb://mongodb:27017/"
DB_NAME = "waste_management"
COLLECTION_HISTORY = "routes_history"

AGGREGATOR_URL = "http://aggregator-service:5000/run-optimization"
OSRM_URL = "http://osrm-backend:5000"

# Configuration Visuelle
COLORS_PALETTE = [
    "red", "blue", "green", "purple", "orange",
    "darkred", "cadetblue", "darkgreen", "darkblue", "black"
]

# Mapping Codes Poubelles -> Libellés UI
BIN_TYPE_MAPPING = {
    "VER": "Verre 🟢",
    "REC": "Recyclable 🟡",
    "ORG": "Organique 🟤",
    "TOU": "Tout-Venant ⚫"
}

# --- UTILITAIRES ---

def format_duration_human(seconds: int) -> str:
    """Convertit des secondes en format lisible (ex: 1h 15m)."""
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{int(hours)}h {int(minutes):02d}m"
    return f"{int(minutes)} min"

def extract_waste_type_from_truck_id(truck_id: str) -> str:
    """
    Extrait le type de déchet du nom du camion généré par l'aggregator.
    Ex: 'T1 (glass)' -> 'glass'
    """
    if "(" in truck_id and ")" in truck_id:
        try:
            return truck_id.split("(")[1].split(")")[0]
        except:
            return "Autre"
    return "Général"

# --- COUCHE DONNÉES (BACKEND) ---

@st.cache_resource
def init_mongo_connection() -> Optional[pymongo.database.Database]:
    """
    Établit la connexion persistante à MongoDB.
    Utilise le cache de Streamlit pour éviter les reconnexions multiples.
    """
    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.server_info()
        return client[DB_NAME]
    except Exception as e:
        st.error(f"⛔ Erreur critique : Impossible de connecter MongoDB ({e})")
        return None

def fetch_route_geometry(waypoints: List[str]) -> Optional[dict]:
    """
    Interroge OSRM pour obtenir la géométrie précise (GeoJSON) entre des points.

    Args:
        waypoints: Liste de chaines "lon,lat".
    """
    if len(waypoints) < 2:
        return None

    # Nettoyage et formatage des coordonnées
    clean_waypoints = []
    for wp in waypoints:
        try:
            lon, lat = wp.split(',')
            clean_waypoints.append(f"{float(lon):.6f},{float(lat):.6f}")
        except ValueError:
            continue

    if not clean_waypoints:
        return None

    coords_str = ";".join(clean_waypoints)
    url = f"{OSRM_URL}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

    try:
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            return resp.json()['routes'][0]['geometry']
    except requests.RequestException:
        return None
    return None

def parse_bin_id(point_id: str) -> Tuple[str, str]:
    """
    Décode un ID technique (ex: PBL-0123-VER) en informations lisibles.

    Returns:
        (Type_Lisible, Identifiant_Court)
    """
    if "DEPOT" in point_id:
        return "Dépôt", "Centre Logistique"

    parts = point_id.split('-')
    # Format attendu : PBL-{NUMERO}-{CODE}
    if len(parts) >= 3:
        code = parts[-1]
        label = BIN_TYPE_MAPPING.get(code, code)
        return label, parts[1]

    return "Inconnu", point_id

def trigger_optimization() -> dict:
    """Appelle le service Aggregator pour lancer le calcul VRP."""
    try:
        response = requests.post(AGGREGATOR_URL, timeout=70)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- COUCHE PRÉSENTATION (UI) ---

def render_sidebar(history_collection) -> list:
    """Gère la barre latérale : Contrôles et Sélection de l'historique."""
    st.sidebar.title("🎮 Contrôle")

    # 1. Bouton Action
    if st.sidebar.button("🚀 GÉNÉRER TOURNEES", type="primary"):
        with st.spinner("Calcul en cours..."):
            result = trigger_optimization()
            if result.get('status') == 'success':
                st.sidebar.success(f"Succès ! {result['routes_count']} tournées.")
                time.sleep(1.5)
                st.rerun()
            elif result.get('status') == 'no_action':
                st.sidebar.info("Rien à faire (aucune poubelle critique).")
            else:
                st.sidebar.error(f"Échec : {result.get('message')}")

    st.sidebar.markdown("---")

    # 2. Sélecteur d'Historique
    st.sidebar.header("📅 Historique")

    # Récupération des dates disponibles (Tri décroissant)
    cursor = history_collection.find({}, {"timestamp": 1}).sort("timestamp", -1)
    history_docs = list(cursor)

    if not history_docs:
        st.sidebar.warning("Aucune donnée disponible.")
        return []

    # Conversion UTC -> Paris pour l'affichage
    options_map = {}
    for doc in history_docs:
        ts_utc = doc["timestamp"].replace(tzinfo=datetime.max.tzinfo).replace(tzinfo=None) # Ensure naive first if needed or handle tz
        ts_utc = doc["timestamp"]
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=ZoneInfo("UTC"))

        ts_paris = ts_utc.astimezone(PARIS_TZ)
        label_str = ts_paris.strftime("%d/%m/%Y à %H:%M:%S")
        options_map[label_str] = doc["_id"]

    selected_label = st.sidebar.selectbox("Choisir une simulation :", options=list(options_map.keys()), index=0)
    selected_id = options_map[selected_label]

    # Chargement de la simulation complète
    simulation_data = history_collection.find_one({"_id": selected_id})
    all_routes = simulation_data.get("routes", [])

    # 3. Filtre par Type de Déchet
    st.sidebar.markdown("---")

    # Identification des types disponibles dans cette simulation
    available_types = set()
    for r in all_routes:
        w_type = extract_waste_type_from_truck_id(r.get("truck_id", ""))
        available_types.add(w_type)

    st.sidebar.header(f"📍 Filtres ({len(all_routes)} trajets)")

    selected_types = st.sidebar.multiselect(
        "Filtrer par type :",
        options=sorted(list(available_types)),
        default=sorted(list(available_types))
    )

    # Filtrage effectif
    routes_filtered = [
        r for r in all_routes
        if extract_waste_type_from_truck_id(r.get("truck_id", "")) in selected_types
    ]

    # 4. Liste des trajets (Avec Durée)

    # Boutons de masse
    col1, col2 = st.sidebar.columns(2)
    if col1.button("Tout cocher"):
        for i in range(len(all_routes)): st.session_state[f"chk_{i}"] = True
    if col2.button("Tout décocher"):
        for i in range(len(all_routes)): st.session_state[f"chk_{i}"] = False

    selected_indices = []

    with st.sidebar.container():

        if not routes_filtered:
            st.sidebar.info("Aucun trajet pour ce type.")

        for route in routes_filtered:
            original_idx = all_routes.index(route)

            tid = route.get("truck_id", "Inconnu")
            nb_stops = len(route.get("stops", [])) - 2  # -2 pour retirer DEPOT start/end
            load = route.get("total_load", 0)
            duration_sec = route.get("total_time_seconds", 0)

            duration_str = format_duration_human(duration_sec)

            key = f"chk_{original_idx}"
            if key not in st.session_state:
                st.session_state[key] = True

            # Label enrichi avec la durée
            label = f"{tid}\n⏱️ {duration_str} | 📦 {int(load)}kg"

            if st.checkbox(label, key=key):
                selected_indices.append(original_idx)

    return [all_routes[i] for i in selected_indices], selected_label


def render_map(routes_to_display: list):
    """Génère et affiche la carte Folium."""
    m = folium.Map(location=NICE_COORDS, zoom_start=13)

    max_duration_seconds = 0
    progress_bar = st.progress(0)

    for idx, route in enumerate(routes_to_display):
        truck_id = route.get("truck_id", "Unknown")
        stops = route.get("stops", [])
        duration = route.get("total_time_seconds", 0)

        # Tracking du temps max (Makespan)
        if duration > max_duration_seconds:
            max_duration_seconds = duration

        # Assignation couleur (déterministe basé sur le hash du nom)
        color = COLORS_PALETTE[abs(hash(truck_id)) % len(COLORS_PALETTE)]
        waypoints_poly = []

        # --- A. Création des Marqueurs ---
        for stop in stops:
            waypoints_poly.append(f"{stop['lon']},{stop['lat']}")

            is_depot = "DEPOT" in stop['point_id']
            icon_color = "black" if is_depot else color
            icon_name = "home" if is_depot else "trash"

            # Contenu Popup HTML
            bin_type, bin_ref = parse_bin_id(stop['point_id'])
            collected = stop.get('load_after_visit', 0)

            if is_depot:
                popup_html = f"<b>🏢 DÉPÔT</b><br>Camion: {truck_id}"
            else:
                popup_html = f"""
                <div style="font-family: sans-serif; min-width: 140px;">
                    <h5 style="margin:0; color:{color}">🗑️ {bin_type}</h5>
                    <hr style="margin: 4px 0;">
                    <b>Ref:</b> {bin_ref}<br>
                    <b>Camion:</b> {truck_id}<br>
                    <b>Charge:</b> {collected:.1f} kg
                </div>
                """

            folium.Marker(
                location=[stop['lat'], stop['lon']],
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')
            ).add_to(m)

        # --- B. Tracé de la Route (Polyline) ---
        geo_data = fetch_route_geometry(waypoints_poly)
        if geo_data:
            dur_str = format_duration_human(duration)
            folium.GeoJson(
                geo_data,
                name=f"Route {truck_id}",
                style_function=lambda x, col=color: {'color': col, 'weight': 4, 'opacity': 0.8},
                tooltip=f"{truck_id} ({dur_str})"
            ).add_to(m)

        # Mise à jour barre de progression
        progress_bar.progress((idx + 1) / len(routes_to_display))

    progress_bar.empty()
    return m, max_duration_seconds


# --- MAIN ---

def main():
    st.set_page_config(page_title=PAGE_TITLE, layout=LAYOUT)
    st.title(PAGE_TITLE)

    db = init_mongo_connection()
    if db is None:
        st.stop()

    # 1. Gestion Sidebar & Sélection
    selected_routes, sim_label = render_sidebar(db[COLLECTION_HISTORY])

    if not selected_routes:
        st.info("Aucune tournée sélectionnée ou historique vide.")
        return

    # 2. Rendu de la Carte
    map_obj, max_time = render_map(selected_routes)

    # 3. Affichage Statistiques (KPIs)
    st.markdown("### Indicateurs")
    kpi1, kpi2, kpi3 = st.columns(3)

    with kpi1:
        st.metric("📅 Date", sim_label)
    with kpi2:
        st.metric("🚛 Flotte Active", f"{len(selected_routes)} Camions")
    with kpi3:
        st.metric("⏱️ Fin Opération", format_duration_human(max_time))
    # 4. Affichage final
    st_folium(map_obj, width=1600, height=700)


if __name__ == "__main__":
    main()