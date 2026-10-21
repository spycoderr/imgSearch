"""Places stage (P1.8): where each photo with GPS was taken, from GeoNames, offline.

Records the nearest city, its district, state and country, and the nearest landmark within
`places.landmark_max_km` (`store.places.save_place`), and writes their names as the photo's
`place` text, so text search finds "Jaipur" or "India Gate". Photos without GPS are marked
done with nothing stored.
"""

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord
from imgsearch.sources.geonames import Gazetteer
from imgsearch.store.places import delete_place, save_place


class PlacesStage:
    """`gazetteer` is for tests; by default `load` reads the files `imgsearch geo download`
    fetched (it fails, naming that command, if they are missing)."""

    name = "places"
    requires = ["ingest"]  # ingest reads the GPS

    def __init__(self, config: Config, gazetteer: Gazetteer | None = None) -> None:
        self.version = config.stages[self.name]
        self._given = gazetteer
        self._gazetteer: Gazetteer | None = None

    def load(self, ctx: Context) -> None:
        self._gazetteer = self._given or Gazetteer.load(ctx.config.places)

    def unload(self) -> None:
        self._gazetteer = None

    def run(self, image: ImageRecord, ctx: Context) -> None:
        if self._gazetteer is None:
            raise RuntimeError("PlacesStage.run called before load")
        if image.lat is None or image.lon is None:
            delete_place(ctx.conn, image.id)
            return
        save_place(ctx.conn, image.id, self._gazetteer.lookup(image.lat, image.lon))
