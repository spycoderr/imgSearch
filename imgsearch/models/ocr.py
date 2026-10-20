"""OCR with Apple Vision through ocrmac (plan D6)."""

from pathlib import Path

from ocrmac.ocrmac import text_from_image
from PIL import Image

from imgsearch.config import OcrConfig


class AppleVisionOCR:
    """`OCREngine` backed by Vision's VNRecognizeTextRequest. Needs no model download."""

    def __init__(self, config: OcrConfig) -> None:
        self.recognition_level = config.recognition_level
        self.languages = config.languages

    def read(self, path: Path) -> str:
        """Return the text in the image, one recognised line per `\\n`, in Vision's reading
        order. An image without text gives ""."""
        with Image.open(path) as image:
            lines = text_from_image(
                image,
                recognition_level=self.recognition_level,
                language_preference=self.languages,
                detail=False,
            )
        return "\n".join(lines)
