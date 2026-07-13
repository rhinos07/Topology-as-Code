# warehouse-definitions

Warehouse-as-Code: deklarative, versionierte Beschreibung von Lagerstrukturen,
Materialfluss-Kommunikation und Prozessstrategien (Nachschub, Bewegungsregeln)
als YAML, validiert per CI, kompiliert zu Runtime-Entities.

## Grundprinzip

| Ebene | Was | Änderungsfrequenz | Wer ändert |
|---|---|---|---|
| `elements/` | Wiederverwendbare Templates (Regaltypen etc.) | sehr selten | Techniker/Architekt |
| `customers/<kunde>/structure/` | Physische Lagerstruktur eines Kunden | selten (bei Umbau) | Techniker, strenger Review |
| `customers/<kunde>/strategies/` | Nachschub-, Bewegungs-, Slotting-Regeln | häufig | Logistikplaner, lockerer Review |

Laufzeitzustand (aktueller Bestand, Belegung, Verfügbarkeit von Ressourcen)
lebt **nicht** hier, sondern in der Runtime-Datenbank des WMS. Diese Repos
beschreiben nur den **Soll-Zustand** der Struktur und der Regeln — analog zu
Terraform: der Code beschreibt die Infrastruktur, nicht deren aktuellen
Live-Status.

## Repo-Struktur

```
warehouse-definitions/
├── schemas/              # JSON Schema zur Validierung aller YAML-Dateien
├── elements/             # Wiederverwendbare Templates (Rack-Typen etc.)
├── customers/
│   └── <kunde>/
│       ├── warehouse.yaml        # Top-Level, importiert die anderen Dateien
│       ├── structure/            # Physische Struktur
│       │   ├── storage.yaml      # Storage Types + Storage-Point-Generatoren
│       │   ├── lanes.yaml        # Fördertechnik / Lanes / Conveyor-Segmente
│       │   └── mfr.yaml          # Meldepunkte, SPS, Telegramm-Aktionen
│       └── strategies/           # Prozessregeln
│           ├── replenishment.yaml
│           └── movement_rules.yaml
├── tools/
│   └── validate.py       # Validierungs-Script (Schema + Konsistenzchecks)
├── docs/
│   └── entity-glossary.md
└── .github/workflows/validate.yaml   # CI-Pipeline (Beispiel, ggf. nach TeamCity portieren)
```

## Schnellstart

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python tools/validate.py customers/example_customer/warehouse.yaml
```

## Kernkonzepte (Kurzreferenz)

- **storage_point** — kleinste physische/logische Lagereinheit (früher "Bin").
  Kann Regalplatz oder Blockplatz sein, unterscheidet sich in Zugriffsmodell
  (`direct` vs. `lifo`). Details: `docs/entity-glossary.md`.
- **storage_type** — Lagerbereich, der storage_points gruppiert (z. B. Hochregal).
- **activity_area** — funktionale Querschnitts-Gruppierung, orthogonal zur
  physischen Hierarchie (ein storage_point kann in mehreren activity_areas sein).
- **reporting_point** (Meldepunkt) — Kommunikationspunkt zwischen WMS und SPS,
  wird technisch immer auch als storage_point abgebildet.
- **movement_rule** — definiert erlaubte/verbotene Warenbewegungen zwischen
  Bereichen. Zwei Policies: `default_allow` (manuelle Bereiche, Ausnahmen
  explizit) vs. `explicit_only` (automatisierte/Fördertechnik-Bereiche, jede
  Route muss explizit existieren).
- **replenishment_strategy** — Nachschub-Regeln (min/max, order-getrieben,
  zero-stock, prädiktiv), referenziert Struktur, ist aber selbst keine Struktur.

Vollständiges Glossar: [`docs/entity-glossary.md`](docs/entity-glossary.md)

## Nächste Schritte für dieses Repo

- [ ] JSON Schemas in `schemas/` vervollständigen (aktuell Grundgerüst)
- [ ] `tools/validate.py` um Konsistenzchecks erweitern (Referenz-Integrität
      zwischen Dateien: verweist jede `movement_rule` auf existierende
      `storage_type`s?)
- [ ] Storage-Point-Generator-Logik implementieren (Template → konkrete Punkte)
- [ ] Compiler-Schritt: YAML → Runtime-Entities (Linq2db-Modell)
- [ ] Optional: Import-Mapper für AutomationML (CAEX) als alternative Quelle
- [ ] TeamCity-Pipeline statt/zusätzlich zu GitHub Actions einrichten
