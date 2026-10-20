"""OCR stage: read the text in each derivative and store it in `ocr` and `text_fts`."""

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord, OCREngine
from imgsearch.models.ocr import AppleVisionOCR
from imgsearch.store.text import save_ocr


class OcrStage:
    """Run an `OCREngine` on the derivative (never the original) of each ingested image.

    `engine` is for tests; by default `load` builds `AppleVisionOCR` from `config.models.ocr`.
    """

    name = "ocr"
    requires = ["ingest"]

    def __init__(self, config: Config, engine: OCREngine | None = None) -> None:
        self.version = config.stages[self.name]
        self._given_engine = engine
        self._engine: OCREngine | None = None

    def load(self, ctx: Context) -> None:
        engine = self._given_engine
        self._engine = AppleVisionOCR(ctx.config.models.ocr) if engine is None else engine

    def unload(self) -> None:
        self._engine = None

    def run(self, image: ImageRecord, ctx: Context) -> None:
        if self._engine is None:
            raise RuntimeError("OcrStage.run called before load")
        if image.derivative_path is None:
            raise ValueError(f"image {image.id} has no derivative_path")
        text = self._engine.read(ctx.config.derivatives_root / image.derivative_path)
        save_ocr(ctx.conn, image.id, text)
