# Entitäts-Glossar

Begriffe angelehnt an SAP EWM/MFS-Terminologie (für Anschlussfähigkeit ans
Branchenvokabular), aber eigenständig benannt, wo es sinnvoller Sinn ergab
(`storage_point` statt "Bin").

## Struktur

| Begriff | Bedeutung |
|---|---|
| `warehouse` | Oberste Ebene, ein Lagerkomplex/eine Halle |
| `storage_type` | Lagerbereich, gruppiert `storage_point`s (z.B. Hochregal, Blocklager) |
| `section` | Unterteilung eines `storage_type` nach Eigenschaften (z.B. Zugriffsfrequenz) |
| `storage_point` | Kleinste physische/logische Lagereinheit (früher "Storage Bin"). Regalplatz oder Blockplatz. |
| `storage_point_generator` | Generiert `storage_point`s aus einem Raster statt sie einzeln aufzuzählen |
| `activity_area` | Funktionale Querschnitts-Gruppierung, orthogonal zur physischen Hierarchie. Ein `storage_point` kann in mehreren `activity_area`s gleichzeitig sein. |
| `work_center` | Physische Einheit für Aktivitäten wie Packen, Verwiegen |
| `door` / `staging_area` | Tore für Wareneingang/-ausgang |
| `lane` / `conveyor_segment` | Physische Verbindung/Fördertechnik zwischen Bereichen (**"kann"**) |
| `reporting_point` (Meldepunkt) | Kommunikationspunkt zwischen WMS und SPS; wird technisch immer auch als `storage_point` abgebildet |
| `resource` / `vehicle` | Ausführendes Element. `resource` = WMS-gesteuert, `vehicle` = SPS-autonom mit eigenem Auftragspuffer |

## Regalplatz vs. Blockplatz

| | Regalplatz (`access_model: rack`) | Blockplatz (`access_model: block`) |
|---|---|---|
| Zugriff | Jeder Punkt einzeln erreichbar (`access_order: direct`) | Nur von vorne/oben (`access_order: lifo`) |
| Kapazität | Meist 1 Ladeeinheit pro `storage_point` | Mehrere Ladeeinheiten pro `storage_point` (Tiefe x Höhe) |
| Artikelmischung | Beliebig | Meist nur ein Artikel gleichzeitig (`homogeneity_required`) |
| Typisch für | Hochregal, Shuttle-Lager | Großmengen, Saisonware |

## Prozessregeln (keine physische Struktur, eigener Lifecycle)

| Begriff | Bedeutung |
|---|---|
| `movement_rule` | Definiert, ob eine Warenbewegung fachlich erlaubt ist (**"darf"**) — unabhängig von physischer Erreichbarkeit (`lane`) |
| `movement_policy` | `default_allow` (manuelle Bereiche, nur Verbote explizit) vs. `explicit_only` (Fördertechnik-Bereiche, jede Route muss explizit definiert sein) |
| `replenishment_strategy` | Nachschub-Regel: `min_max`, `quantity_based`, `zero_stock`, `predictive` |

## Wichtige Architekturprinzipien

1. **Struktur vs. Laufzeitzustand**: Diese YAML-Dateien beschreiben nur den
   Soll-Zustand. Aktuelle Belegung, Verfügbarkeit, Bestand leben in der
   Runtime-Datenbank — analog zu Terraform-Code vs. tatsächlichem
   Cloud-Ressourcen-Status.

2. **`lane`/`conveyor_segment` ("kann") vs. `movement_rule` ("darf")**:
   Ein Shuttle kann physisch von der Kühlzone in die Ambient-Zone fahren
   (Lane existiert), aber die Bewegung von Ware ist fachlich verboten
   (Kühlkette). Beide Ebenen bewusst getrennt modelliert.

3. **`movement_policy` je nach Automatisierungsgrad**:
   - Manuelle Bereiche: physische Flexibilität ist immer da (Stapler kann
     überall hinfahren, wo ein Weg existiert) → `default_allow` mit
     expliziten Verboten reicht aus.
   - Automatisierte/Fördertechnik-Bereiche: die Infrastruktur selbst ist
     die Einschränkung, es gibt keine implizite Flexibilität → jede Route
     muss explizit existieren (`explicit_only`).

4. **`storage_point_generator` statt Enumeration**: Bei tausenden
   `storage_point`s wird eine flache Auflistung unhandhabbar (Git-Diffs,
   Merge-Konflikte). Templates + explizite `exceptions` halten die Datei
   kompakt, unabhängig von der physischen Lagergröße.

5. **Struktur (`structure/`) vs. Strategien (`strategies/`)**: Getrennte
   Ordner/Lifecycle, weil sie unterschiedlich oft geändert werden und
   unterschiedliche Zielgruppen haben (Techniker vs. Logistikplaner).
