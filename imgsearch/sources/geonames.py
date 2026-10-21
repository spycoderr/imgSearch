"""GeoNames (CC BY 4.0) for offline reverse geocoding (P1.8, plan D22).

`download` fetches the dump files once into `places.dir`; `Gazetteer.load` reads them and
`Gazetteer.lookup(lat, lon)` finds a photo's city, district, state, country and landmark
with numpy (haversine, prefiltered by a bounding box). Nothing here is used at search time.

File formats (https://download.geonames.org/export/dump/readme.txt): `<CC>.zip` holds
`<CC>.txt`, one feature per line, 19 tab-separated columns (geonameid, name, asciiname,
alternatenames, latitude, longitude, feature class, feature code, country code, cc2,
admin1 code, admin2 code, ...). `admin1CodesASCII.txt` and `admin2Codes.txt` are
`code<TAB>name<TAB>asciiname<TAB>geonameid`, with codes like `IN.07` and `IN.07.0001`.
`countryInfo.txt` has `#` comment lines; column 4 is the country name, 16 its geonameid.
"""

import hashlib
import io
import math
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

from imgsearch.config import PlacesConfig

ADMIN_FILES = ("admin1CodesASCII.txt", "admin2Codes.txt", "countryInfo.txt")
MANIFEST = "MANIFEST.tsv"
EARTH_KM = 6371.0088  # mean Earth radius
KM_PER_DEG_LAT = 111.2


def file_names(config: PlacesConfig) -> list[str]:
    return [f"{cc}.zip" for cc in config.countries] + list(ADMIN_FILES)


def missing_files(config: PlacesConfig) -> list[str]:
    return [name for name in file_names(config) if not (config.dir / name).is_file()]


@dataclass(frozen=True)
class Downloaded:
    name: str
    size: int
    sha256: str
    skipped: bool  # already there; not downloaded again


def download(config: PlacesConfig, client: httpx.Client, force: bool = False) -> list[Downloaded]:
    """Download each GeoNames file that isn't in `config.dir` yet (all of them with `force`),
    via a temp file and rename. Writes MANIFEST.tsv: name, size, SHA-256, URL and the time
    each file was downloaded."""
    config.dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(config.dir / MANIFEST)
    results = []
    for name in file_names(config):
        path = config.dir / name
        url = f"{config.base_url.rstrip('/')}/{name}"
        skipped = path.is_file() and not force
        if not skipped:
            tmp = path.with_name(path.name + ".part")
            with client.stream("GET", url) as response, tmp.open("wb") as out:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    out.write(chunk)
            tmp.replace(path)
        size, sha = path.stat().st_size, _sha256(path)
        if not skipped or name not in manifest:
            when = datetime.now().isoformat(timespec="seconds")
            manifest[name] = [str(size), sha, url, when]
        results.append(Downloaded(name, size, sha, skipped))
    lines = ["name\tsize\tsha256\turl\tdownloaded"]
    lines += ["\t".join([name, *row]) for name, row in sorted(manifest.items())]
    (config.dir / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


def _read_manifest(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()[1:]]
    return {row[0]: row[1:] for row in rows if row}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Feature:
    """One GeoNames feature: its id, display name and every name it goes by (name,
    ASCII name and alternate names, in any script)."""

    geonameid: int
    name: str
    names: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class Place:
    """Where a photo was taken. Any part may be None (nothing within range)."""

    city: Feature | None = None
    city_km: float | None = None
    district: Feature | None = None
    state: Feature | None = None
    country: Feature | None = None
    landmark: Feature | None = None
    landmark_km: float | None = None

    def features(self) -> list[Feature]:
        """The parts that are set, most specific first, each once."""
        parts = (self.landmark, self.city, self.district, self.state, self.country)
        return list({f.geonameid: f for f in parts if f is not None}.values())


class _Points:
    """Coordinates plus one item per point, for nearest-point lookups."""

    def __init__(self, coords: list[tuple[float, float]], items: list) -> None:
        array = np.array(coords, dtype=np.float64).reshape(-1, 2)
        self.lat, self.lon = array[:, 0], array[:, 1]
        self.items = items

    def nearest(self, lat: float, lon: float, max_km: float):
        """(item, km) of the nearest point within `max_km`, or None. Only points inside the
        bounding box around (lat, lon) are measured."""
        dlat = max_km / KM_PER_DEG_LAT
        dlon = max_km / (KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
        box = np.flatnonzero((np.abs(self.lat - lat) <= dlat) & (np.abs(self.lon - lon) <= dlon))
        if box.size == 0:
            return None
        km = haversine_km(lat, lon, self.lat[box], self.lon[box])
        best = int(np.argmin(km))
        if km[best] > max_km:
            return None
        return self.items[box[best]], float(km[best])


def haversine_km(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Great-circle distance in km from (lat, lon) to each of (lats, lons), in degrees."""
    p1, p2 = math.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons) - math.radians(lon)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


class Gazetteer:
    """The GeoNames features of the configured countries, ready for `lookup`."""

    def __init__(
        self,
        config: PlacesConfig,
        cities: _Points,
        settlements: _Points,
        landmarks: _Points,
        admin: dict[str, Feature],
    ) -> None:
        self.config = config
        self.cities = cities  # items: Feature
        self.settlements = settlements  # every populated place; items: admin2 or admin1 code
        self.landmarks = landmarks  # items: Feature
        self.admin = admin  # "IN" (country), "IN.07" (state), "IN.07.0001" (district)

    @classmethod
    def load(cls, config: PlacesConfig) -> "Gazetteer":
        """Read the downloaded files. Raises FileNotFoundError naming `imgsearch geo download`
        if any is missing."""
        if missing := missing_files(config):
            raise FileNotFoundError(
                f"GeoNames files missing in {config.dir}: {', '.join(missing)}."
                " Run `imgsearch geo download`"
            )
        city_codes, landmark_codes = set(config.city_codes), set(config.landmark_codes)
        cities: tuple[list, list] = ([], [])
        settlements: tuple[list, list] = ([], [])
        landmarks: tuple[list, list] = ([], [])
        admin_names: dict[int, tuple[str, ...]] = {}  # alternate names of admin features
        for cc in config.countries:
            for row in read_features(config.dir / f"{cc}.zip", cc):
                fclass, code = row[6], row[7]
                coords = (float(row[4]), float(row[5]))
                if fclass == "P":
                    a1, a2 = row[10], row[11]
                    admin_code = ".".join(c for c in (row[8], a1, a2 if a1 else "") if c)
                    settlements[0].append(coords)
                    settlements[1].append(admin_code)
                    if code in city_codes and int(row[14] or 0) >= config.city_min_population:
                        cities[0].append(coords)
                        cities[1].append(_feature(row))
                elif fclass == "A" and code in ("ADM1", "ADM2", "PCLI"):
                    admin_names[int(row[0])] = _names(row)
                if code in landmark_codes:
                    landmarks[0].append(coords)
                    landmarks[1].append(_feature(row))
        admin = _admin_features(config.dir, set(config.countries), admin_names)
        return cls(config, _Points(*cities), _Points(*settlements), _Points(*landmarks), admin)

    def lookup(self, lat: float, lon: float) -> Place:
        cfg = self.config
        city = self.cities.nearest(lat, lon, cfg.city_max_km)
        settlement = self.settlements.nearest(lat, lon, cfg.city_max_km)
        landmark = self.landmarks.nearest(lat, lon, cfg.landmark_max_km)
        code = settlement[0] if settlement else ""
        parts = code.split(".")
        return Place(
            city=city[0] if city else None,
            city_km=city[1] if city else None,
            district=self.admin.get(code) if len(parts) == 3 else None,
            state=self.admin.get(".".join(parts[:2])) if len(parts) >= 2 else None,
            country=self.admin.get(parts[0]) if code else None,
            landmark=landmark[0] if landmark else None,
            landmark_km=landmark[1] if landmark else None,
        )


def read_features(zip_path: Path, cc: str) -> Iterator[list[str]]:
    """The rows of `<cc>.txt` inside `zip_path`, read without extracting it."""
    with zipfile.ZipFile(zip_path) as z, z.open(f"{cc}.txt") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            row = line.rstrip("\n").split("\t")
            if len(row) != 19:
                raise ValueError(f"{zip_path.name}: expected 19 columns, got {len(row)}")
            yield row


def _names(row: list[str]) -> tuple[str, ...]:
    alternates = [n for n in row[3].split(",") if n]
    return tuple(dict.fromkeys([row[1], row[2], *alternates]))


def _feature(row: list[str]) -> Feature:
    return Feature(int(row[0]), row[1], _names(row))


def _admin_features(
    folder: Path, countries: set[str], alternates: dict[int, tuple[str, ...]]
) -> dict[str, Feature]:
    """Countries, states (admin1) and districts (admin2) of `countries`, keyed by code, with
    the alternate names of their feature rows in the country dumps."""
    admin: dict[str, Feature] = {}
    for line in (folder / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        row = line.split("\t")
        if row[0] in countries and row[16]:
            gid = int(row[16])
            admin[row[0]] = Feature(gid, row[4], (row[4], *alternates.get(gid, ())))
    for name in ("admin1CodesASCII.txt", "admin2Codes.txt"):
        for line in (folder / name).read_text(encoding="utf-8").splitlines():
            code, display, ascii_name, gid = line.split("\t")
            if code.split(".")[0] in countries and gid:
                names = (display, ascii_name, *alternates.get(int(gid), ()))
                admin[code] = Feature(int(gid), display, tuple(dict.fromkeys(names)))
    return admin
