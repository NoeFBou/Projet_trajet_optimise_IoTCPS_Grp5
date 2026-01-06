from flask import Flask, request, jsonify
from pymongo import MongoClient
import os
from datetime import datetime

app = Flask(__name__)

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
# use English database name
db = client["logistics"]
trucks_collection = db["trucks"]



# Seed database with initial data
def seed_data():
    
    sample_trucks = [
        {
            "truck_id": "T001",
            "waste_type": "household",
            "address": "12 Rue des Forges, 75010 Paris",
            "sector": "North",
            "capacity": 18, # In kilograms
            "volume": 30, # In cubic meters
            "status": "active",
            "schedule": [
                {"date": "2025-11-20", "start_time": "08:00", "end_time": "12:00"},
                {"date": "2025-11-21", "start_time": "13:00", "end_time": "18:00"}
            ]
        },
        {
            "truck_id": "T002",
            "waste_type": "recyclables",
            "address": "45 Avenue du Sud, 34000 Montpellier",
            "sector": "South",
            "capacity": 25, # In kilograms
            "volume": 30, # In cubic meters
            "status": "maintenance",
            "schedule": [
                {"date": "2025-11-22", "start_time": "09:00", "end_time": "14:00"}
            ]
        },
        {
            "truck_id": "T003",
            "waste_type": "glass",
            "address": "8 Boulevard de l'Est, 67000 Strasbourg",
            "sector": "East",
            "capacity": 12,
            "status": "active",
            "schedule": [
                {"date": "2025-11-23", "start_time": "07:00", "end_time": "11:00"}
            ]
        }
    ]

    trucks_collection.insert_many(sample_trucks)
    print("Database initialized with truck data.")


seed_data()





# API endpoint to list trucks with filtering and pagination
@app.route("/api/trucks", methods=["GET"])
def list_trucks():
    try:
        # Read filter parameters from the query string
        waste_type = request.args.get("waste_type")
        sector = request.args.get("sector")
        status = request.args.get("status")
        min_capacity = request.args.get("min_capacity", type=int)
        max_capacity = request.args.get("max_capacity", type=int)
        date = request.args.get("date")   # filter trucks available on a given day

        # Build the query based on provided filters
        query = {}

        if waste_type:
            query["waste_type"] = waste_type
        if sector:
            query["sector"] = sector
        if status:
            query["status"] = status
        if min_capacity is not None:
            query["capacity"] = {"$gte": min_capacity}
        if max_capacity is not None:
            query.setdefault("capacity", {})["$lte"] = max_capacity
        if date:
            # search for a schedule containing the date
            query["schedule.date"] = date

        # Search trucks in the database
        trucks = list(trucks_collection.find(query, {"_id": 0}))

        return jsonify({"count": len(trucks), "trucks": trucks}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
  

# Run the Flask app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
