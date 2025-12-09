import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Importation de votre logique métier
from level_calculator_with_dempster_shafer import WasteBinMonitor

app = FastAPI(
    title="Waste Bin Fusion API",
    description="API de fusion de données capteurs utilisant la théorie de Dempster-Shafer pour déterminer le niveau de remplissage.",
    version="1.0.0"
)

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "my-super-secret-auth-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "waste_management")

monitor = WasteBinMonitor()

class BinDataInput(BaseModel):
    bin_id: str = Field(..., description="Identifiant unique de la poubelle", example="BIN-774")
    statut: str = Field("normal", description="Statut opérationnel", example="normal")
    bin_type: str = Field(..., description="Type de déchet (verre, plastique, organique...)", example="verre")

    # Capteurs
    weight_value: float = Field(..., description="Poids mesuré en kg", example=35.2)
    us_value1: float = Field(..., description="Distance Ultrason 1 (cm)", example=15.0)
    us_value2: float = Field(..., description="Distance Ultrason 2 (cm)", example=14.5)
    ir_value25: int = Field(..., description="Capteur IR 25% (0 ou 1)", example=1)
    ir_value50: int = Field(..., description="Capteur IR 50% (0 ou 1)", example=1)
    ir_value75: int = Field(..., description="Capteur IR 75% (0 ou 1)", example=0)

    # Métadonnées
    Battery_level: float = Field(..., description="Niveau de batterie (%)", example=88.5)
    GPS_value: str = Field(..., description="Coordonnées GPS", example="43.616,7.055")
    Measurement_date: str = Field(..., description="Timestamp de la mesure", example="2023-12-01T14:30:00Z")

    class Config:
        schema_extra = {
            "example": {
                "bin_id": "BIN-TEST-01",
                "statut": "normal",
                "bin_type": "plastique",
                "weight_value": 2.5,
                "us_value1": 5,
                "us_value2": 8,
                "ir_value25": 1,
                "ir_value50": 1,
                "ir_value75": 1,
                "Battery_level": 92.0,
                "GPS_value": "48.85,2.35",
                "Measurement_date": "2023-10-27T10:00:00Z"
            }
        }


class BinDataOutput(BaseModel):
    bin_id: str
    level_bin_fullness: str
    level_description: str
    statut: str
    processed_at: float


STATE_TRANSLATION = {
    'E1': 'Vide (0-30%)',
    'E2': 'Moyen (30-60%)',
    'E3': 'Rempli (60-80%)',
    'E4': 'Presque plein (80-90%)',
    'E5': 'Plein (90-100%)',
    'Inconnu': 'Indéterminé'
}


def write_to_influx(data: BinDataInput, level_code: str, level_desc: str, conflict: float = 0.0):
    try:
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        write_api = client.write_api(write_options=SYNCHRONOUS)

        point = Point("bin_status") \
            .tag("bin_id", data.bin_id) \
            .tag("bin_type", data.bin_type) \
            .tag("statut", data.statut) \
            .field("weight", float(data.weight_value)) \
            .field("level_code", level_code) \
            .field("level_desc", level_desc) \
            .field("conflict", float(conflict))

        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
        client.close()
    except Exception as e:
        print(f"Warning: InfluxDB write failed: {e}")


@app.post("/fusion", response_model=BinDataOutput, tags=["Calcul Niveau"])
def compute_bin_level(input_data: BinDataInput):
    """
    **Calcule le niveau de remplissage** à partir des capteurs bruts.

    - Effectue la fusion de données (Poids, US, IR)
    - Enregistre le résultat dans InfluxDB
    - Retourne l'état estimé (E1 à E5)
    """

    monitor_inputs = {
        'type': input_data.bin_type.lower(),
        'weight': input_data.weight_value,
        'us1': input_data.us_value1,
        'us2': input_data.us_value2,
        'ir25': input_data.ir_value25,
        'ir50': input_data.ir_value50,
        'ir75': input_data.ir_value75
    }

    try:
        etat, m_final = monitor.compute_level(monitor_inputs)

        conflit = m_final.get(frozenset(), 0.0)

        description = STATE_TRANSLATION.get(etat, "Inconnu")

        write_to_influx(input_data, str(etat), description, conflit)

        return BinDataOutput(
            bin_id=input_data.bin_id,
            level_bin_fullness=str(etat),
            level_description=description,
            statut=input_data.statut,
            processed_at=time.time()
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur interne: {str(e)}")


@app.get("/health", tags=["Système"])
def health_check():
    return {"status": "ok", "service": "WasteBinMonitor"}