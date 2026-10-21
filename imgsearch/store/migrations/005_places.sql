-- P1.8 places from GPS (offline reverse geocoding with GeoNames, plan D22).
-- places: each GeoNames feature some photo resolves to (city, district, state, country or
--   landmark), with its display name.
-- place_names: every name a place goes by (GeoNames name, ASCII name and alternate names in
--   any script), as a lookup key: NFC, casefolded, words joined by one space. Search
--   resolves the rewriter's `place` here, so it needs no GeoNames files.
-- photo_places: one row per photo with GPS that the `places` stage resolved; ids point
--   into places, NULL where nothing was within range. Distances in km.
-- text_fts gains rows of kind 'place' (written by store.text); no schema change needed.

CREATE TABLE places (
  geonameid INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE place_names (
  key TEXT NOT NULL,
  geonameid INTEGER NOT NULL REFERENCES places(geonameid),
  PRIMARY KEY (key, geonameid)
);

CREATE TABLE photo_places (
  image_id TEXT PRIMARY KEY REFERENCES images(id),
  city_id INTEGER REFERENCES places(geonameid),
  city_km REAL,
  district_id INTEGER REFERENCES places(geonameid),
  state_id INTEGER REFERENCES places(geonameid),
  country_id INTEGER REFERENCES places(geonameid),
  landmark_id INTEGER REFERENCES places(geonameid),
  landmark_km REAL
);
