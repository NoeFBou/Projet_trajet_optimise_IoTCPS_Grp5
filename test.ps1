# --- CONFIGURATION ---
$OSRM_DATA_DIR = "osrm-data"
$DATASET_DIR = "dataset"
$MAP_URL = "https://download.geofabrik.de/europe/france/provence-alpes-cote-d-azur-latest.osm.pbf"
$MAP_FILE = "map.osm.pbf"
$OSRM_IMAGE = "osrm/osrm-backend"

$BD_TOPO_URL = "https://data.geopf.fr/telechargement/download/BDTOPO/BDTOPO_3-3_TOUSTHEMES_GPKG_LAMB93_D066_2024-03-15/BDTOPO_3-3_TOUSTHEMES_GPKG_LAMB93_D066_2024-03-15.7z" 


if (!(Test-Path -Path $OSRM_DATA_DIR)) { New-Item -ItemType Directory -Path $OSRM_DATA_DIR | Out-Null }
if (!(Test-Path -Path $DATASET_DIR)) { New-Item -ItemType Directory -Path $DATASET_DIR | Out-Null }


$MapPath = "$OSRM_DATA_DIR\$MAP_FILE"
$OsrmReady = Test-Path "$OSRM_DATA_DIR\map.osrm.edges"

if (!$OsrmReady) {

    if (!(Test-Path $MapPath)) {
        Write-Host "   > Téléchargement de la carte PACA..."
        Invoke-WebRequest -Uri $MAP_URL -OutFile $MapPath
    }

    Write-Host "   > Extraction du graphe (Step 1/3)..."
    docker run -t -v "${PWD}/$OSRM_DATA_DIR`:/data" $OSRM_IMAGE osrm-extract -p /opt/car.lua /data/$MAP_FILE
    
    Write-Host "   > Partitionnement (Step 2/3)..."
    docker run -t -v "${PWD}/$OSRM_DATA_DIR`:/data" $OSRM_IMAGE osrm-partition /data/map.osrm
    
    Write-Host "   > Customisation (Step 3/3)..."
    docker run -t -v "${PWD}/$OSRM_DATA_DIR`:/data" $OSRM_IMAGE osrm-customize /data/map.osrm

    Write-Host "OSRM est pret " 
} else {
    Write-Host "OSRM est deja init"
}

# --- 4. LANCEMENT ---
Write-Host "Lancement de Docker Compose..."
docker-compose up --build -d

Write-Host "   - Dashboard : http://localhost:8501"
Write-Host "   - API Camions : http://localhost:5001"
