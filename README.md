# Projet_trajet_optimise_IoTCPS_Grp5



# Projet d'optimisation de trajet de collecte de dechet

## Contexte

Projet réalisé dans le cadre du cours de systèmes intelligents autonomes (2025 - 2026) 

Ce développement est mock d'un projet conceptualisé pendant les cours de _Fondements et défis des systèmes Cyber-Physiques_ et de _L'iot aux systèmes Cyber-Physiques_.



---

## Architecture

Une solution complète basée sur une architecture services pour optimiser la collecte des déchets urbains. Ce projet intègre la fusion de données capteurs (Dempster-Shafer), la gestion de flotte en temps réel et le calcul d'itinéraires optimisés (VRP).
Le système repose sur une communication asynchrone via Kafka/mqtt/http et une orchestration conteneurisée avec Docker.

schéma :
je le met ici quand tout sera fini 


### Flux de données simplifié :

1. **Capteurs (Simulation)** : Envoi de données brutes (Poids, Ultrasons, Infrarouge).
2. **Service Fusion** : Analyse et décision sur le niveau de remplissage (Algorithme Dempster-Shafer).
3. **Aggregator** : Centralisation de l'état des poubelles et déclenchement des collectes.
4. **Service VRP** : Calcul des tournées optimales via **OSRM** et **Google OR-Tools**.
5. **Dashboard** : Visualisation cartographique et contrôle opérationnel.

---

## Fonctionnalités

* **Routing** : Calcul et générations des trajets avec un moteur OSRM local (OpenStreetMap).
* **Optimisation Multi-types** : Génération de tournées distinctes selon le type de déchet (Verre, Recyclable, Organique, etc.).
* **Fusion de Données** : Utilisation de la méthode de Dempster-Shafer pour déduire le niveau de remplissage des déchets d'une poubelle à partir des données des capteurs.
* **Dashboard** : Interface Streamlit pour génerer les trajets de collecte et les visualiser sur une carte.
* **Simulateur des capteurs** :
* **Filtrage des données capteurs** :
* **Résilience** : Architecture tolérante aux pannes : Buffers Kafka, Fallbacks mathématiques si OSRM indisponible(Haversine), deduction des anomalies capteur.

---

## Services & Composants

| Service            | Technologie             | Description                                                       |
|--------------------|-------------------------|-------------------------------------------------------------------|
| **Fusion Service** | Python, Kafka           | Calcule l'état (E1-E5) des poubelles à partir des capteurs bruts. |
| **Aggregator**     | Python, Flask           | Orchestre les demandes d'optimisation.                            |
| **VRP Service**    | OR-Tools, FastAPI       | Résout le problème de tournée de véhicules.                       |
| **Dashboard**      | Streamlit, Folium       | Interface utilisateur pour les opérateurs et la visualisation.    |
| **OSRM Backend**   | C++                     | Moteur de routage géographique local.                             |
| **Infrastructure** | Kafka, Zookeeper, Mongo | Bus de messages et persistance des données.                       |
| **Simulateur**     | Python, mosquitto       | Simule les données capteurs des poubelles(avec bruitage/erreurs)  |
| **Filtre**         | Python, http            | ...                                                               |
| **Truck service**  | Python, http            | Expose les informations des camions disponibles                   |


---

## Installation

### Prérequis

* [Docker](https://www.docker.com/) & Docker Compose
* Un environnement Linux (ou WSL2 sous Windows) recommandé.

### 1. Clonage du dépôt

```bash
git clone https://github.com/NoeFBou/Projet_trajet_optimise_IoTCPS_Grp5.git
cd Projet_trajet_optimise_IoTCPS_Grp5

```

### 2. Initialisation des Données Géographiques (OSRM)

Le moteur de routage nécessite l'extraction de la carte (ex: Nice/PACA). Un script est fourni pour automatiser cette étape.

```bash
# Rendre le script exécutable
chmod +x init_project.sh

# Lancer l'initialisation (Télécharge et prépare la carte)
./init_project.sh

```

### 3. Démarrage de la Stack

```bash
docker-compose up --build

```

*La première exécution peut prendre quelques minutes pour construire les images et initialiser Kafka.*

---

## ️ Utilisation

Une fois les conteneurs lancés, les interfaces suivantes sont accessibles :

### Dashboard

* **URL :** `http://localhost:8501`
* **Fonctions :**
  * Visualiser les poubelles pleines sur la carte.
  * Générer des trajets optimisés pour la collecte en cliquant sur un bouton.
  * Filtrer les trajets par type de camion (Verre, Organique...).
  * Consulter l'historique des collectes.

---

## Détails Techniques

### Algorithme de Fusion (Dempster-Shafer)

Le `Fusion Service` combine trois sources de croyance :

1. **Poids** (Densité estimée par type).
2. **Ultrasons** (Distance mesurée).
3. **Infrarouge** (Barrières optiques à 25%, 50%, 75%).
Cela permet de détecter des anomalies (ex: poubelle légère mais volumineuse -> cartons non pliés) et d'assigner un niveau de confiance (`mass`) à l'état de la poubelle.

### Optimisation VRP (Vehicle Routing Problem)

Le problème est modélisé comme un CVRP (Capacitated VRP).

* **Solver :** Google OR-Tools.
* **Matrice de Coûts :** Calculée via OSRM (distances réelles) avec un fallback Haversine.
* **Contraintes :** Capacité des camions, temps de travail max, compatibilité des déchets.

---

## Licence

Ce projet est sous licence MIT - voir le fichier [LICENSE](https://www.google.com/search?q=LICENSE) pour plus de détails.

---
