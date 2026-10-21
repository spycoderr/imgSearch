"""P1.8 places: GeoNames download and lookup, the places stage, resolving a query's place,
the place filter and its fallback, and where places are shown. Tiny synthetic GeoNames
files in tmp_path; no downloads."""

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest
from typer.testing import CliRunner

from imgsearch.chat.bot import caption_line
from imgsearch.cli import app
from imgsearch.config import Config, load_config
from imgsearch.eval.main import evaluate
from imgsearch.eval.queries import EvalQuery
from imgsearch.eval.run import CachedRewriter, run_eval
from imgsearch.interfaces import Context, ParsedQuery, Result
from imgsearch.search.hybrid import Searcher
from imgsearch.search.show import response_lines
from imgsearch.search.text import TextRetriever
from imgsearch.search.vector import VectorRetriever
from imgsearch.sources.geonames import (
    MANIFEST,
    Gazetteer,
    download,
    file_names,
    haversine_km,
)
from imgsearch.stages.embed import save_embedding
from imgsearch.stages.places import PlacesStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.places import place_key, place_labels, resolve_place
from imgsearch.store.text import save_caption, save_ocr

# ---- synthetic GeoNames ----

# (geonameid, name, alternate names, lat, lon, class, code, admin1, admin2, population)
FEATURES = [
    (1269750, "Republic of India", "India,Bharat,भारत,IN", 22.0, 79.0, "A", "PCLI", "00", "", 0),
    (1258899, "Rajasthan", "राजस्थान", 26.5, 74.0, "A", "ADM1", "24", "", 0),
    (1273293, "Delhi", "NCT,दिल्ली", 28.6, 77.2, "A", "ADM1", "07", "", 0),
    (1270260, "Haryana", "", 29.0, 76.0, "A", "ADM1", "10", "", 0),
    (1253626, "Uttar Pradesh", "UP", 27.0, 80.0, "A", "ADM1", "36", "", 0),
    (1269516, "Jaipur", "Jaipur District", 27.0, 75.8, "A", "ADM2", "24", "08", 0),
    (1261481, "New Delhi", "", 28.6, 77.2, "A", "ADM2", "07", "01", 0),
    (1270641, "Gurugram district", "Gurgaon district", 28.4, 77.0, "A", "ADM2", "10", "05", 0),
    (1272939, "Gautam Buddha Nagar", "", 28.5, 77.4, "A", "ADM2", "36", "09", 0),
    # Cities and other populated places
    (
        1269515,
        "Jaipur",
        "Jaipur City,Jaypur,जयपुर",
        26.9196,
        75.7878,
        "P",
        "PPLA",
        "24",
        "08",
        3_046_163,
    ),  # fmt: skip
    (1278935, "Amer", "Amber,आमेर", 26.9850, 75.8500, "P", "PPL", "24", "08", 13_000),
    (1261481_0, "Kunda", "", 26.9900, 75.8560, "P", "PPL", "24", "08", 0),  # a village
    (1261481_1, "New Delhi", "नई दिल्ली", 28.6139, 77.2090, "P", "PPLC", "07", "01", 317_797),
    (1270642, "Gurugram", "Gurgaon", 28.4595, 77.0266, "P", "PPLA2", "10", "05", 876_824),
    (1261481_2, "Sector 18", "", 28.5708, 77.3261, "P", "PPLX", "36", "09", 0),  # Noida
    (
        1277333,
        "Bengaluru",
        "Bangalore,बंगलौर",
        12.9719,
        77.5937,
        "P",
        "PPLA",
        "19",
        "20",
        5_104_047,
    ),  # fmt: skip
    # Landmarks, and a hotel (not a landmark code)
    (1274220, "India Gate", "", 28.6129, 77.2295, "S", "MNMT", "07", "01", 0),
    (6619409, "Amber Fort", "Amer Fort,आमेर क़िला", 26.9855, 75.8513, "S", "FT", "24", "08", 0),
    (9999001, "Hotel Near The Gate", "", 28.6131, 77.2297, "S", "HTL", "07", "01", 0),
]
ADMIN1 = [
    ("IN.24", "Rajasthan", 1258899),
    ("IN.07", "Delhi", 1273293),
    ("IN.10", "Haryana", 1270260),
    ("IN.36", "Uttar Pradesh", 1253626),
    ("IN.19", "Karnataka", 1267701),
    ("NP.01", "Bagmati", 1111111),  # another country: not loaded
]
ADMIN2 = [
    ("IN.24.08", "Jaipur", 1269516),
    ("IN.07.01", "New Delhi", 1261481),
    ("IN.10.05", "Gurugram district", 1270641),
    ("IN.36.09", "Gautam Buddha Nagar", 1272939),
]
COUNTRY_INFO = (
    "#ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital\tArea\tPopulation\tContinent\ttld"
    "\tCurrencyCode\tCurrencyName\tPhone\tPostal\tRegex\tLanguages\tgeonameid\tneighbours"
    "\tEquivalentFipsCode\n"
    "IN\tIND\t356\tIN\tIndia\tNew Delhi\t3287590\t1352617328\tAS\t.in\tINR\tRupee\t91"
    "\t######\t\ten-IN,hi\t1269750\tCN,NP\t\n"
    "NP\tNPL\t524\tNP\tNepal\tKathmandu\t147181\t28087871\tAS\t.np\tNPR\tRupee\t977"
    "\t#####\t\tne,en\t1282988\tCN,IN\t\n"
)

INDIA_GATE = (28.6128, 77.2294)
AMBER_FORT = (26.9856, 75.8512)
JAIPUR = (26.9200, 75.7880)
NOIDA = (28.5710, 77.3260)


def geo_row(f: tuple) -> str:
    gid, name, alts, lat, lon, fclass, code, a1, a2, pop = f
    cols = [gid, name, name, alts, lat, lon, fclass, code, "IN", "", a1, a2, "", "", pop]
    cols += ["", "0", "Asia/Kolkata", "2026-01-01"]
    return "\t".join(str(c) for c in cols)


def geo_files() -> dict[str, bytes]:
    """The four files `geo download` fetches for countries [IN], as bytes."""
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("IN.txt", "\n".join(geo_row(f) for f in FEATURES) + "\n")
        z.writestr("readme.txt", "GeoNames")
    admin = lambda rows: "".join(f"{c}\t{n}\t{n}\t{g}\n" for c, n, g in rows)  # noqa: E731 (one-off)
    return {
        "IN.zip": buffer.getvalue(),
        "admin1CodesASCII.txt": admin(ADMIN1).encode(),
        "admin2Codes.txt": admin(ADMIN2).encode(),
        "countryInfo.txt": COUNTRY_INFO.encode(),
    }


@pytest.fixture
def geo(config: Config) -> Config:
    """The synthetic GeoNames files in places.dir (under tmp_path)."""
    config.places.dir.mkdir(parents=True, exist_ok=True)
    for name, data in geo_files().items():
        (config.places.dir / name).write_bytes(data)
    return config


@pytest.fixture
def gazetteer(geo: Config) -> Gazetteer:
    return Gazetteer.load(geo.places)


# ---- download ----


def serve_geo(requests: list[str]) -> httpx.Client:
    files = geo_files()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=files[name]) if name in files else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_fetches_each_file_once_and_records_checksums(config: Config):
    requests: list[str] = []
    results = download(config.places, serve_geo(requests))

    assert [r.name for r in results] == file_names(config.places)
    assert file_names(config.places) == [
        "IN.zip",
        "admin1CodesASCII.txt",
        "admin2Codes.txt",
        "countryInfo.txt",
    ]
    assert requests == [f"/export/dump/{name}" for name in file_names(config.places)]
    files = geo_files()
    manifest = (config.places.dir / MANIFEST).read_text(encoding="utf-8").splitlines()
    assert manifest[0] == "name\tsize\tsha256\turl\tdownloaded"
    for r in results:
        assert not r.skipped
        assert (config.places.dir / r.name).read_bytes() == files[r.name]
        assert r.sha256 == hashlib.sha256(files[r.name]).hexdigest()
        row = next(line.split("\t") for line in manifest if line.startswith(r.name + "\t"))
        assert row[1:4] == [str(r.size), r.sha256, f"{config.places.base_url}/{r.name}"]
    assert not list(config.places.dir.glob("*.part"))

    again: list[str] = []
    assert all(r.skipped for r in download(config.places, serve_geo(again)))
    assert again == []  # downloaded once
    forced: list[str] = []
    download(config.places, serve_geo(forced), force=True)
    assert len(forced) == 4


def test_failed_download_leaves_no_file(config: Config):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        download(config.places, client)
    assert not (config.places.dir / "IN.zip").exists()


def test_cli_geo_download_has_force(config_path: Path):
    result = CliRunner().invoke(app, ["--config", str(config_path), "geo", "download", "--help"])
    assert result.exit_code == 0 and "--force" in result.output


# ---- lookup ----


def test_haversine():
    km = haversine_km(28.0, 77.0, np.array([29.0, 28.0]), np.array([77.0, 77.0]))
    assert km[0] == pytest.approx(111.2, abs=0.1)  # one degree of latitude
    assert km[1] == 0.0


def test_missing_files_name_the_download_command(config: Config):
    with pytest.raises(FileNotFoundError, match="imgsearch geo download"):
        Gazetteer.load(config.places)


def test_lookup_india_gate(gazetteer: Gazetteer):
    p = gazetteer.lookup(*INDIA_GATE)
    assert p.landmark.name == "India Gate" and p.landmark_km < 0.05
    assert p.city.name == "New Delhi" and p.city_km == pytest.approx(1.97, abs=0.05)
    assert (p.district.name, p.state.name, p.country.name) == ("New Delhi", "Delhi", "India")
    # Alternate names come from the admin rows of the country dump.
    assert "दिल्ली" in p.state.names and "भारत" in p.country.names


def test_lookup_city_needs_population_and_landmark_needs_its_code(gazetteer: Gazetteer):
    p = gazetteer.lookup(*AMBER_FORT)
    assert p.city.name == "Amer"  # Kunda (a village, population 0) is nearer
    assert p.landmark.name == "Amber Fort"
    assert (p.district.name, p.state.name) == ("Jaipur", "Rajasthan")
    # The hotel is nearer India Gate's photos than nothing, but HTL isn't a landmark code.
    assert gazetteer.lookup(28.6131, 77.2297).landmark.name == "India Gate"


def test_district_and_state_follow_the_nearest_settlement(gazetteer: Gazetteer):
    """Noida: the nearest city is New Delhi, but the photo is in Uttar Pradesh."""
    p = gazetteer.lookup(*NOIDA)
    assert p.city.name == "New Delhi"
    assert (p.district.name, p.state.name) == ("Gautam Buddha Nagar", "Uttar Pradesh")
    assert p.landmark is None  # India Gate is over 1 km away


def test_far_from_everything_finds_nothing(gazetteer: Gazetteer):
    p = gazetteer.lookup(51.5, -0.12)  # London: no Indian place within 30 km
    assert p.features() == []


# ---- the stage ----


def add_photo(conn, image_id: str, gps: tuple[float, float] | None, taken: str | None = None):
    lat, lon = gps or (None, None)
    with conn:
        conn.execute(
            "INSERT INTO images (id, orig_relpath, lat, lon, taken_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (image_id, f"{image_id}.jpg", lat, lon, taken, "2026-10-21T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO stage_status (image_id, stage, version, state, updated_at)"
            " VALUES (?, 'ingest', '2', 'done', '2026-10-21')",
            (image_id,),
        )


@pytest.fixture
def library(geo: Config, conn: sqlite3.Connection) -> sqlite3.Connection:
    """Photos at India Gate, Amber Fort, in Jaipur city and Noida (GPS), one without GPS
    whose caption names Jaipur, and one without GPS or place words. Places stage run."""
    add_photo(conn, "gate", INDIA_GATE, "2024-01-26T10:00:00")
    add_photo(conn, "fort", AMBER_FORT, "2023-12-30T10:00:00")
    add_photo(conn, "city", JAIPUR, "2023-12-31T10:00:00")
    add_photo(conn, "noida", NOIDA, "2024-01-26T18:00:00")
    add_photo(conn, "caption", None, "2019-05-01T10:00:00")
    add_photo(conn, "plain", None, "2024-01-26T12:00:00")
    save_caption(conn, "caption", "A fort on a hill in Jaipur at sunset.", "m")
    save_ocr(conn, "plain", "photo of a fort")
    counts = run_stage(PlacesStage(geo), Context(config=geo, conn=conn))
    assert counts == {"done": 6, "failed": 0}
    return conn


def test_stage_records_each_level_and_skips_photos_without_gps(library):
    rows = {
        r["image_id"]: dict(r)
        for r in library.execute(
            "SELECT pp.image_id, c.name AS city, d.name AS district, s.name AS state,"
            " n.name AS country, l.name AS landmark, pp.landmark_km FROM photo_places pp"
            " LEFT JOIN places c ON c.geonameid = pp.city_id"
            " LEFT JOIN places d ON d.geonameid = pp.district_id"
            " LEFT JOIN places s ON s.geonameid = pp.state_id"
            " LEFT JOIN places n ON n.geonameid = pp.country_id"
            " LEFT JOIN places l ON l.geonameid = pp.landmark_id"
        )
    }
    assert set(rows) == {"gate", "fort", "city", "noida"}  # no row without GPS
    assert rows["fort"]["city"] == "Amer" and rows["fort"]["landmark"] == "Amber Fort"
    assert rows["fort"]["landmark_km"] < 0.05
    assert (rows["fort"]["district"], rows["fort"]["state"]) == ("Jaipur", "Rajasthan")
    assert rows["fort"]["country"] == "India"
    assert rows["city"]["landmark"] is None
    done = library.execute(
        "SELECT COUNT(*) FROM stage_status WHERE stage = 'places' AND state = 'done'"
    ).fetchone()[0]
    assert done == 6


def test_stage_writes_place_names_as_search_text(library):
    texts = dict(library.execute("SELECT image_id, text FROM text_fts WHERE kind = 'place'"))
    assert texts["gate"] == "India Gate New Delhi Delhi India"
    assert texts["fort"] == "Amber Fort Amer Jaipur Rajasthan India"
    assert "caption" not in texts
    hits = TextRetriever(library).retrieve(ParsedQuery("india gate", "india gate"), 10)
    assert hits[0].image_id == "gate"


def test_stage_is_idempotent_and_reruns_on_a_version_bump(library, geo: Config):
    ctx = Context(config=geo, conn=library)
    assert run_stage(PlacesStage(geo), ctx) == {"done": 0, "failed": 0}
    bumped_version = str(int(geo.stages["places"]) + 1)
    bumped = geo.model_copy(update={"stages": {**geo.stages, "places": bumped_version}})
    with library:  # the photo lost its GPS (e.g. corrected): the re-run removes its place
        library.execute("UPDATE images SET lat = NULL, lon = NULL WHERE id = 'gate'")
    stage = PlacesStage(bumped)
    assert run_stage(stage, Context(config=bumped, conn=library)) == {"done": 6, "failed": 0}
    assert library.execute("SELECT COUNT(*) FROM photo_places").fetchone()[0] == 3
    assert library.execute("SELECT COUNT(*) FROM text_fts WHERE kind = 'place'").fetchone()[0] == 3


def test_stage_without_geo_files_fails_loudly(config: Config, conn):
    add_photo(conn, "gate", INDIA_GATE)
    with pytest.raises(FileNotFoundError, match="imgsearch geo download"):
        run_stage(PlacesStage(config), Context(config=config, conn=conn))


def test_cli_run_places_and_status(config_path: Path, geo: Config, conn):
    add_photo(conn, "gate", INDIA_GATE)
    add_photo(conn, "plain", None)
    runner = CliRunner()
    # No pen drive needed: the stage reads only the DB and the GeoNames files.
    result = runner.invoke(app, ["--config", str(config_path), "run", "places"])
    assert result.exit_code == 0, result.output
    assert "places: 2 processed (2 done, 0 failed)" in result.output
    status = runner.invoke(app, ["--config", str(config_path), "status"])
    assert "gps: 1 of 2 photos; places resolved: 1 (1 with a landmark)" in status.output


# ---- resolving the query's place ----


@pytest.mark.parametrize(
    ("place", "names"),
    [
        ("Jaipur", {"Jaipur"}),  # the city and the district
        ("jaipur", {"Jaipur"}),
        ("जयपुर", {"Jaipur"}),
        ("Jaypur", {"Jaipur"}),
        ("Rajasthan", {"Rajasthan"}),
        ("राजस्थान", {"Rajasthan"}),
        ("India Gate", {"India Gate"}),
        ("the India Gate", {"India Gate"}),
        ("Amber", {"Amer"}),
        ("Amer Fort", {"Amber Fort"}),
        ("Amber Fort, Jaipur", {"Amber Fort"}),  # first part that resolves
        ("Delhi", {"Delhi"}),
        ("Noida, Uttar Pradesh", {"Uttar Pradesh"}),
        ("India", {"India"}),
    ],
)
def test_resolve_place(library, place: str, names: set[str]):
    resolved = resolve_place(library, place)
    assert resolved is not None
    found = {
        library.execute("SELECT name FROM places WHERE geonameid = ?", (g,)).fetchone()[0]
        for g in resolved.geonameids
    }
    assert found == names


def test_jaipur_resolves_to_the_city_and_the_district(library):
    assert resolve_place(library, "Jaipur").geonameids == (1269515, 1269516)


@pytest.mark.parametrize("place", ["park", "sofa", "home", "beach", "", None, "UP", "Bengaluru"])
def test_unresolvable_places_are_ignored(library, place):
    """Common words, too-short names, and real places no photo was taken at."""
    assert resolve_place(library, place) is None


def test_alternate_names_resolve_once_a_photo_is_there(library, geo: Config):
    """Bangalore/Bengaluru and Gurgaon/Gurugram are alternate names of one place."""
    add_photo(library, "blr", (12.97, 77.59))
    add_photo(library, "ggn", (28.46, 77.03))
    run_stage(PlacesStage(geo), Context(config=geo, conn=library))
    for a, b in [("Bangalore", "Bengaluru"), ("बंगलौर", "Bengaluru"), ("Gurgaon", "Gurugram")]:
        assert resolve_place(library, a).geonameids == resolve_place(library, b).geonameids


def test_place_key():
    assert place_key("  The  INDIA-Gate ") == "india gate"
    assert place_key("जयपुर") == "जयपुर"
    assert place_key("The") == "the"


# ---- the place filter in search ----


class FakeEmbedder:
    def embed_images(self, paths):
        raise AssertionError("search must not embed images")

    def embed_text(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0], dtype=np.float32)


class FakeRewriter:
    def __init__(self, place: str | None, date_range: tuple[str, str] | None = None):
        self.place, self.date_range = place, date_range

    def rewrite(self, query: str) -> ParsedQuery:
        return ParsedQuery(query, query, place=self.place, date_range=self.date_range)


ALL = {"gate", "fort", "city", "noida", "caption", "plain"}


def searcher(conn, config: Config, place, date_range=None) -> Searcher:
    """A vector retriever that ranks every photo, and the text retriever."""
    for image_id in ALL:
        save_embedding(conn, image_id, "test-model", np.array([1.0, 0.0], dtype=np.float32), 2)
    embedder_config = config.models.embedder.model_copy(update={"name": "test-model", "dim": 2})
    retrievers = {
        "vector": VectorRetriever(conn, FakeEmbedder(), embedder_config),
        "text": TextRetriever(conn),
    }
    return Searcher(conn, config.search, FakeRewriter(place, date_range), retrievers)


def found(response) -> set[str]:
    return {r.image_id for r in response.results}


def test_vector_search_keeps_photos_from_the_place_or_naming_it(library, geo: Config):
    response = searcher(library, geo, "Jaipur").search("jaipur ka kila", k=10, signals=["vector"])
    # GPS in Jaipur city or Jaipur district, or the caption says Jaipur.
    assert found(response) == {"fort", "city", "caption"}
    assert response.place_filter == "Jaipur" and not response.place_fallback


def test_a_state_matches_all_its_cities(library, geo: Config):
    response = searcher(library, geo, "Rajasthan").search("trip", k=10, signals=["vector"])
    assert found(response) == {"fort", "city"}


def test_text_search_is_filtered_too(library, geo: Config):
    response = searcher(library, geo, "India Gate").search("fort", k=10, signals=["text"])
    # "fort" matches only photos away from India Gate, so the place is dropped.
    assert found(response) == {"fort", "caption", "plain"}
    assert response.place_fallback
    response = searcher(library, geo, "Rajasthan").search("fort", k=10, signals=["text"])
    assert found(response) == {"fort"}  # "plain" and "caption" say fort but aren't there


def test_an_unresolvable_place_is_ignored(library, geo: Config):
    response = searcher(library, geo, "park").search("park", k=10, signals=["vector"])
    assert found(response) == ALL
    assert response.query.place == "park" and response.query.place_filter is None
    assert response.place_filter is None and not response.place_fallback


def test_place_and_dates_must_both_hold(library, geo: Config):
    jan26 = ("2024-01-26", "2024-01-26")
    response = searcher(library, geo, "Delhi", jan26).search("q", k=10, signals=["vector"])
    assert found(response) == {"gate"}  # noida and plain are from that day, not from Delhi
    assert response.date_filter == jan26 and response.place_filter == "Delhi"


def test_no_photo_from_the_place_on_those_dates_drops_the_place_first(library, geo: Config):
    jan26 = ("2024-01-26", "2024-01-26")
    response = searcher(library, geo, "Jaipur", jan26).search("q", k=10, signals=["vector"])
    assert found(response) == {"gate", "noida", "plain"}  # that day, any place
    assert response.place_fallback and response.place_filter is None
    assert response.date_filter == jan26 and not response.date_fallback


def test_then_the_dates(library, geo: Config):
    never = ("2001-01-01", "2001-01-01")
    response = searcher(library, geo, "Jaipur", never).search("q", k=10, signals=["vector"])
    assert found(response) == ALL
    assert response.place_fallback and response.date_fallback


def test_place_filter_off(library, geo: Config):
    s = searcher(library, geo, "Jaipur")
    response = s.search("q", k=10, signals=["vector"], place_filter=False)
    assert found(response) == ALL
    assert response.query.place_filter.label == "Jaipur" and response.place_filter is None


# ---- showing places ----


def test_results_carry_date_and_place(library, geo: Config):
    response = searcher(library, geo, "Amber Fort").search("q", k=10, signals=["vector"])
    [fort] = [r for r in response.results if r.image_id == "fort"]
    assert fort.place == "Amber Fort, Amer, Jaipur, Rajasthan"
    assert fort.date == "2023-12-30T10:00:00"
    assert place_labels(library, ["gate", "plain"]) == {"gate": "India Gate, New Delhi, Delhi"}


def test_cli_lines_show_the_place_and_each_results_place(library, geo: Config):
    lines = response_lines(searcher(library, geo, "Jaipur").search("q", signals=["vector"]))
    assert "place: Jaipur" in lines
    assert "    2023-12-30 · Amber Fort, Amer, Jaipur, Rajasthan" in lines
    assert "    2019-05-01" in lines  # no GPS: the date alone

    never = ("2001-01-01", "2001-01-01")
    s = searcher(library, geo, "Jaipur", never)
    lines = response_lines(s.search("q", signals=["vector"]))
    assert "place: no photos from Jaipur; showing results from all places" in lines
    lines = response_lines(s.search("q", signals=["vector"], place_filter=False))
    assert "place: Jaipur found but not used (place filter off)" in lines


def test_cli_search_accepts_no_place_filter(config_path: Path, library):
    args = ["--config", str(config_path), "search", "fort", "--signals", "text", "--no-rewrite"]
    result = CliRunner().invoke(app, [*args, "--no-place-filter"])
    assert result.exit_code == 0, result.output
    assert "fort.jpg" in result.output and "Amber Fort, Amer, Jaipur, Rajasthan" in result.output


def test_bot_caption_shows_the_place():
    r = Result("x", "a.jpg", None, None, "A fort.", 0.1, {}, date="2023-12-30T10:00", place="Amer")
    assert caption_line(2, r) == "2 · 2023-12-30 · Amer · A fort."


# ---- eval ----

FORT = EvalQuery("q-fort", "jaipur ka kila", "hinglish", "place", ("fort",), "")


def eval_searcher(conn, config: Config):
    s = searcher(conn, config, "Jaipur")
    rewriter = CachedRewriter(s.rewriter)
    retrievers = {**s.retrievers, "text_no_captions": TextRetriever(conn, kinds=("ocr",))}
    return Searcher(conn, config.search, rewriter, retrievers), rewriter


def test_eval_records_the_place_and_can_switch_the_filter_off(library, geo: Config):
    s, _ = eval_searcher(library, geo)
    on = {r["config"]: r for r in run_eval(s, [FORT])}
    off = {r["config"]: r for r in run_eval(s, [FORT], place_filter=False)}
    assert on["vector+rw"]["place"] == "Jaipur"
    assert on["vector+rw"]["place_resolved"] == "Jaipur"
    assert on["vector+rw"]["place_filter"] == "Jaipur" and not on["vector+rw"]["place_fallback"]
    assert off["vector+rw"]["place_resolved"] == "Jaipur"
    assert off["vector+rw"]["place_filter"] is None
    assert on["vector"]["place"] is None  # no rewrite, no place


def test_eval_meta_and_cli_switch(library, geo: Config, config_path: Path):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("http://localhost:11434", "http://127.0.0.1:9"))
    config = load_config(config_path)  # port 9: no Ollama version lookup answers
    config.eval.queries.parent.mkdir(parents=True, exist_ok=True)
    config.eval.queries.write_text(
        "queries:\n  - {id: q-fort, query: fort, language: english, type: place,"
        " expected_image_ids: [fort], note: ''}\n",
        encoding="utf-8",
    )
    run_dir = evaluate(
        config,
        config_path,
        library,
        lambda _: None,
        build=lambda c, conn: eval_searcher(conn, c),
        place_filter=False,
    )
    assert json.loads((run_dir / "meta.json").read_text())["place_filter"] is False
    result = CliRunner().invoke(app, ["--config", str(config_path), "eval", "--help"])
    assert result.exit_code == 0 and "--no-place-filter" in result.output
