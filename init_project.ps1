# init_project.ps1

Write-Host "--- Démarrage de l'initialisation OSRM ---" -ForegroundColor Cyan

$PBF_FILE = "service_vrp/data/data.osm.pbf"
if (-not (Test-Path $PBF_FILE)) {
    Write-Host "ERREUR : Le fichier $PBF_FILE est introuvable !" -ForegroundColor Red
    Write-Host "Veuillez télécharger un fichier .osm.pbf (ex: geofabrik.de) et le placer dans service_vrp/data/data.osm.pbf"
    exit 1
}

Write-Host "Nettoyage des anciens fichiers OSRM..."
Remove-Item service_vrp/data/*.osrm* -ErrorAction SilentlyContinue

Write-Host "1/3 Extraction du graphe (Cela peut prendre du temps)..."
docker run -t -v "${PWD}/service_vrp/data:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua /data/data.osm.pbf

Write-Host "2/3 Partitionnement..."
docker run -t -v "${PWD}/service_vrp/data:/data" osrm/osrm-backend osrm-partition /data/data.osrm

Write-Host "3/3 Customisation (Calcul des poids)..."
docker run -t -v "${PWD}/service_vrp/data:/data" osrm/osrm-backend osrm-customize /data/data.osrm

Write-Host "--- Initialisation terminée ! ---" -ForegroundColor Green
Write-Host "Tu peux maintenant lancer : docker-compose up --build"