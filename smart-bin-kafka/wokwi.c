#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* ssid = "Wokwi-GUEST"; // Réseau virtuel de Wokwi
const char* password = "";
// Adresse IP de TON PC vu par Wokwi Gateway (souvent 10.0.0.2)
// On enverra les données à notre script Python "Pont" sur le port 5000
const char* serverUrl = "http://10.0.0.2:5000/data"; 


#define TRIG_PIN 5
#define ECHO_PIN 18
#define POT_PIN 34
String binID = "PBL-WOKWI-01";

void setup() {
  Serial.begin(115200);
  
  
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  
  
  WiFi.begin(ssid, password);
  Serial.print("Connexion au WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" Connecté !");
}

void loop() {
  
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long duration = pulseIn(ECHO_PIN, HIGH);
  int distanceCm = duration * 0.034 / 2;

  
  int potValue = analogRead(POT_PIN);
  float weightKg = map(potValue, 0, 4095, 0, 5000) / 100.0; // 0 à 50.0kg

  
  StaticJsonDocument<512> doc;
  doc["bin_id"] = binID;
  doc["bin_type"] = "Verre";
  
  JsonArray sensors = doc.createNestedArray("sensors");
  
  JsonObject s1 = sensors.createNestedObject();
  s1["id"] = "US-01";
  s1["type"] = "ultrasonic";
  s1["value"] = distanceCm;
  s1["unit"] = "cm";

  JsonObject s2 = sensors.createNestedObject();
  s2["type"] = "load_cell";
  s2["value"] = weightKg;
  s2["unit"] = "kg";

  JsonObject status = doc.createNestedObject("status");
  status["battery_level"] = 85;

  String jsonString;
  serializeJson(doc, jsonString);

  //  Envoyer via HTTP POST
  if(WiFi.status() == WL_CONNECTED){
    HTTPClient http;
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");
    
    int httpResponseCode = http.POST(jsonString);
    
    if(httpResponseCode > 0){
      Serial.println("Donnée envoyée: " + jsonString);
    } else {
      Serial.print("Erreur d'envoi: ");
      Serial.println(httpResponseCode);
    }
    http.end();
  }

  delay(5000);
}