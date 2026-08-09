---
name: Weather card design
description: Open-Meteo advanced usage and caching/race patterns for the Home Screen weather card.
---
All data is free/keyless Open-Meteo, fetched client-side (CORS-enabled): forecast API with `minutely_15=precipitation` (2h nowcast, radar-blended where available), current gusts/direction/pressure/cloud cover, hourly uv_index/visibility/dew_point/pressure_msl, 7-day dailies with sunrise/sunset/uv_index_max/precipitation_probability_max; separate air-quality-api host for US AQI (non-fatal parallel fetch).

**Timezone rule:** `timezone=auto` returns all timestamps in the SELECTED CITY's local time (manual city may differ from browser zone). Compute city-local "now" from `utc_offset_seconds` + UTC getters; compare local-time strings or Date.UTC-parsed values (offsets cancel in differences). Nowcast minutes must come from timestamp deltas, not slot-index × 15 (the current slot is partially elapsed).

**Cache rules:** stale-while-revalidate with localStorage persistence. A cache entry is only usable when age is in [0, 3h] (rejects future-dated clocks) AND its `locKey` matches the location that would be fetched now — otherwise a persisted geolocation snapshot shows as the wrong city.

**Forced-load race:** a plain `if (loading) return` guard swallows forced reloads (city change during a background SWR refresh silently no-ops). **How to apply:** forced loads bump a generation ref and proceed; every commit point (success, catch, finally) checks `gen.current === myGen` and discards superseded results.

**Pressure trend:** hourly data starts at city-local midnight, so shortly after midnight there is no 3h lookback — report "unknown", never a fake "steady".
