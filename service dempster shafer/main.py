import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from level_calculator_with_dempster_shafer import WasteBinMonitor

app = FastAPI(title="Service de Fusion de Données Poubelles")

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "my-super-secret-auth-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "waste_management")

monitor = WasteBinMonitor()


class BinDataInput(BaseModel):
    bin_id: str
    statut: str
    bin_type: str

    weight_value: float
    us_value1: float
    us_value2: float
    ir_value25: int
    ir_value50: int
    ir_value75: int

    Battery_level: float
    GPS_value: str
    Measurement_date: str


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
            .field("weight", data.weight_value) \
            .field("ultrasound_1", data.us_value1) \
            .field("ultrasound_2", data.us_value2) \
            .field("level_code", level_code) \
            .field("level_desc", level_desc) \
            .field("battery_level", data.Battery_level) \
            .field("conflict_mass", conflict)

        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
        client.close()
    except Exception as e:
        print(f"Erreur d'écriture InfluxDB: {e}")


@app.post("/fusion", response_model=BinDataOutput)
def compute_bin_level(input_data: BinDataInput):
    """
    Reçoit les données capteurs, fusionne avec Dempster-Shafer,
    enregistre dans InfluxDB et retourne le résultat.
    """

    monitor_inputs = {
        'type': input_data.bin_type.lower(),  # 'verre', 'plastique', etc.
        'weight': input_data.weight_value,
        'us1': input_data.us_value1,
        'us2': input_data.us_value2,
        'ir25': input_data.ir_value25,
        'ir50': input_data.ir_value50,
        'ir75': input_data.ir_value75
    }

    try:

        etat, m_final = monitor.compute_level(monitor_inputs)
        description = STATE_TRANSLATION.get(etat, "Inconnu")

        write_to_influx(input_data, str(etat), description)

        return BinDataOutput(
            bin_id=input_data.bin_id,
            level_bin_fullness=str(etat),
            level_description=description,
            statut=input_data.statut,
            processed_at=time.time()
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de calcul: {str(e)}")


@app.get("/health")
def health_check():
    return {"status": "ok"}