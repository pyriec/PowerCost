# Home Assistant PowerCost

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

**Home Assistant PowerCost** est une intégration personnalisée moderne permettant de calculer avec une haute précision le coût électrique d'un ou plusieurs appareils à partir de diverses entités Home Assistant (puissance instantanée ou compteurs d'énergie périodiques/cumulatifs).

---

## 🌟 Fonctionnalités Principales

- **Modes Tarifaires Flexibles** :
  - **Prix variable** : suit en temps réel l'entité fournissant le prix du kWh (ex: tarifs dynamiques, Nord Pool, etc.).
  - **Heures Creuses / Heures Pleines (HC/HP)** : détection automatique des états de l'entité de tarification (Tempo, Linky, contrat HC/HP).
- **Segmentation Temporelle Précise (Time Slices)** :
  - Si une consommation s'étale sur une transition de tarif (ex: 18h59 HC $\rightarrow$ 19h00 HP), la consommation est découpée et pondérée précisément au prorata du temps passé dans chaque tarif.
- **Support Universel des Sources d'Énergie & Puissance** :
  1. *Puissance instantanée* ($W$ ou $kW$) avec intégration temporelle trapézoïdale et protection contre les coupures prolongées.
  2. *Énergie quotidienne* ($kWh$ ou $Wh$) avec détection automatique de la remise à zéro à minuit.
  3. *Énergie mensuelle* avec détection du changement de mois.
  4. *Énergie annuelle* avec détection du changement d'année.
  5. *Énergie totale* cumulative avec détection des remplacements de compteur ou recalibrages.
- **Persistance & Résilience** :
  - Sauvegarde locale via le gestionnaire de stockage Home Assistant (`Store`).
  - Tolérance aux redémarrages de Home Assistant et restaurations d'états sans perte ni saut de valeur.
- **Reconstruction Historique via Recorder** :
  - Service natif permettant de recalculer le coût passé sur n'importe quelle période à partir des historiques enregistrés dans la base de données Home Assistant.
- **10 Entités Statistiques par Appareil** :
  - Coûts actuels : Jour, Mois, Année, Total.
  - Extrema : Coût max jour, Coût max mois, Coût max année.
  - Moyennes : Coût moyen jour, Coût moyen mois, Coût moyen année.

---

## 📦 Installation

### Méthode 1 : Via HACS (Recommandée)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pyriec&repository=PowerCost&category=integration)

#### Option 1 : En un clic (recommandé)
Cliquez sur le bouton ci-dessus pour ouvrir directement le dépôt dans HACS sur votre instance Home Assistant et lancer le téléchargement.

#### Option 2 : Ajout manuel dans HACS
1. Ouvrez **HACS** dans votre interface Home Assistant.
2. Cliquez sur les trois petits points en haut à droite, puis sélectionnez **Dépôts personnalisés** (*Custom repositories*).
3. Entrez l'URL du dépôt GitHub : `https://github.com/pyriec/PowerCost`.
4. Dans la catégorie, choisissez **Intégration** (*Integration*).
5. Cliquez sur **Ajouter**, puis recherchez **PowerCost** et cliquez sur **Télécharger**.
6. Redémarrez Home Assistant.

### Méthode 2 : Installation Manuelle

1. Téléchargez l'archive du dépôt ou clonez le code.
2. Copiez le répertoire `custom_components/electricity_cost` dans votre dossier `config/custom_components/` de Home Assistant :
   ```text
   config/
   └── custom_components/
       └── electricity_cost/
           ├── __init__.py
           ├── manifest.json
           ├── const.py
           ├── coordinator.py
           ├── ...
   ```
3. Redémarrez Home Assistant.

---

## ⚙️ Configuration

L'intégration se configure entièrement via l'interface graphique de Home Assistant (**Paramètres** $\rightarrow$ **Appareils et services** $\rightarrow$ **Ajouter une intégration** $\rightarrow$ rechercher **PowerCost**).

### Étape 1 : Choix du Mode de Tarification

- **Prix variable via une entité** :
  - Renseignez l'entité fournissant le prix du kWh (ex: `sensor.prix_kwh_variable`).
- **Heures Creuses / Heures Pleines** :
  - Entité prix Heures Creuses (ex: `sensor.prix_heures_creuses`).
  - Entité prix Heures Pleines (ex: `sensor.prix_heures_pleines`).
  - Entité indiquant la période actuelle (ex: `sensor.edf_tempo_couleur` ou `sensor.compteur_linky_tarif`).
  - L'intégration analyse dynamiquement les valeurs prises par cette entité et vous propose de mapper :
    - Valeur Heures Creuses (ex: `HC`, `off_peak`, `creuses`)
    - Valeur Heures Pleines (ex: `HP`, `peak`, `pleines`)

### Étape 2 : Configuration du Premier Appareil

- **Nom de l'appareil** : Nom convivial (ex: `Machine à laver`).
- **Entité source** : Capteur mesurant la puissance ou l'énergie (ex: `sensor.prise_machine_power`).
- **Type de source** :
  - *Puissance instantanée*
  - *Énergie quotidienne*
  - *Énergie mensuelle*
  - *Énergie annuelle*
  - *Énergie totale*
- **Unité** : Optionnel si fournie par l'entité (`W`, `kW`, `Wh`, `kWh`, `MWh`).

---

## 📱 Gestion Multi-Appareils

Pour ajouter d'autres appareils ou modifier la tarification :
1. Allez dans **Paramètres** $\rightarrow$ **Appareils et services**.
2. Cliquez sur **Configurer** sur la carte **PowerCost**.
3. Choisissez :
   - **Ajouter un appareil**
   - **Modifier ou supprimer un appareil**
   - **Modifier la configuration tarifaire**

---

## 📊 Entités Créées par Appareil

Chaque appareil génère automatiquement un périphérique (*Device*) regroupant 10 capteurs :

| Capteur | Description | Classe d'état (`state_class`) | Unité |
| :--- | :--- | :--- | :--- |
| `sensor.<appareil>_cout_aujourd_hui` | Coût cumulé sur la journée en cours | `total` | € |
| `sensor.<appareil>_cout_ce_mois` | Coût cumulé sur le mois en cours | `total` | € |
| `sensor.<appareil>_cout_cette_annee` | Coût cumulé sur l'année en cours | `total` | € |
| `sensor.<appareil>_cout_total` | Coût total cumulé depuis le début | `total_increasing` | € |
| `sensor.<appareil>_cout_max_jour` | Dépense maximale constatée sur 1 jour | `measurement` | € |
| `sensor.<appareil>_cout_max_mois` | Dépense maximale constatée sur 1 mois | `measurement` | € |
| `sensor.<appareil>_cout_max_annee` | Dépense maximale constatée sur 1 an | `measurement` | € |
| `sensor.<appareil>_cout_moyen_jour` | Dépense moyenne par jour actif | `measurement` | € |
| `sensor.<appareil>_cout_moyen_mois` | Dépense moyenne par mois actif | `measurement` | € |
| `sensor.<appareil>_cout_moyen_annee` | Dépense moyenne par an | `measurement` | € |

### Attributs Complémentaires
- `energy_today_kwh`, `energy_month_kwh`, `energy_year_kwh`, `energy_total_kwh`
- `current_price` (tarif applicable)
- `source_entity`, `source_type`
- `last_rebuild_timestamp`

---

## 🛠️ Services Disponibles

### 1. `electricity_cost.rebuild_history`
Reconstruit l'historique de consommation et de coût à partir des enregistrements de la base de données Home Assistant (Recorder).

Exemple d'appel de service dans Outils de développement $\rightarrow$ Actions / Services :
```yaml
action: electricity_cost.rebuild_history
data:
  device_id: "dev_wash" # Optionnel : laisser vide si rebuild_all est vrai
  start_date: "2026-01-01 00:00:00"
  end_date: "2026-03-31 23:59:59" # Optionnel : par défaut maintenant
  rebuild_all: false
```

### 2. `electricity_cost.reset_statistics`
Remet à zéro les statistiques cumulées d'un appareil ou de tous les appareils.
```yaml
action: electricity_cost.reset_statistics
data:
  device_id: "dev_wash"
  rebuild_all: false
```

---

## 🧪 Lancement des Tests

Les tests automatisés sont exécutés avec `pytest` et `pytest-homeassistant-custom-component` :

```bash
# Installation des dépendances de test
pip install pytest pytest-homeassistant-custom-component

# Lancement des tests
PYTHONPATH=. pytest -v tests
```

---

## ⚠️ Limites Connues & Bonnes Pratiques

1. **Rétention du Recorder** : La reconstruction historique dépend directement de la durée de rétention configurée dans votre `recorder` Home Assistant (par défaut 10 jours sauf configuration étendue ou base externe MariaDB/PostgreSQL).
2. **Puissance instantanée** : Les capteurs de puissance doivent rapporter leurs valeurs avec une fréquence raisonnable (ex: au moins une fois toutes les quelques minutes) pour que l'intégration trapézoïdale soit la plus représentative possible.
3. **Coupures très longues** : Si un appareil de puissance ne transmet aucune valeur pendant plus de 2 heures consécutives, l'intervalle est considéré comme inactif pour éviter d'imputer une puissance continue imaginaire pendant une panne.

---

## 📄 Licence

Ce projet est sous licence Apache 2.0.
