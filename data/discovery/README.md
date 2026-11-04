# Local discovery datasets

| File | Contents | Source label |
|---|---|---|
| `sample_places.json` | **Fictional** places near the three demo properties, so the app works offline in development. Only used when `DISCOVERY_SAMPLE_DATA=true` (off by default). | `sample` |
| `osm_snapshot.json` | *(not committed; you create it)* Real OpenStreetMap data around your properties, used when live Overpass search is unavailable. The script saves it in the data volume (`/data/discovery/`), so it survives rebuilds. | `osm_snapshot` |

Create or refresh the snapshot on a machine with internet access (about a minute, a few MB):

```bash
docker compose exec backend python -m scripts.build_osm_snapshot
```

Data © OpenStreetMap contributors, licensed under the ODbL (https://www.openstreetmap.org/copyright).
