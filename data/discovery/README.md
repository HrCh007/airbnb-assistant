# Local discovery datasets

| File | Contents | Source label |
|---|---|---|
| `sample_places.json` | **Fictional** places near the three demo properties, so the app works offline in development. Only used when `DISCOVERY_SAMPLE_DATA=true` (off by default). | `sample` |
| `osm_snapshot.json` | *(not committed; you create it)* Real OpenStreetMap data around your properties, used when live Overpass search is unavailable. The script saves it in the data volume (`/data/discovery/`), so it survives rebuilds. | `osm_snapshot` |

**Build the snapshot once after setup (recommended).** The chatbot then answers neighbourhood questions from it first: instantly, offline, and without depending on the busy public servers.

```bash
docker compose exec backend python -m scripts.build_osm_snapshot
docker compose restart backend
```

The script first asks the public Overpass servers, retrying patiently for a few minutes if they are "at capacity". If that fails it downloads a free OpenStreetMap extract file covering Delhi and reads it locally. You can also give it a file you downloaded yourself:

```bash
docker compose cp delhi.osm.pbf backend:/data/osm/delhi.osm.pbf
docker compose exec backend python -m scripts.build_osm_snapshot --file /data/osm/delhi.osm.pbf
```

Re-run it every month or two to pick up new places.

Data © OpenStreetMap contributors, licensed under the ODbL (https://www.openstreetmap.org/copyright).
