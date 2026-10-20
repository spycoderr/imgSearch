"""Caption stage: describe each derivative with the VLM and store it in captions/text_fts."""

from collections.abc import Callable
from typing import Protocol

from imgsearch.config import CaptionerConfig, Config
from imgsearch.interfaces import Captioner, Context, ImageRecord
from imgsearch.store.text import save_caption


class LoadedCaptioner(Captioner, Protocol):
    """A captioner holding a model in memory until `unload()`."""

    def unload(self) -> None: ...


def _load_mlx_captioner(config: CaptionerConfig) -> LoadedCaptioner:
    # Imported here: mlx-vlm takes ~3 s to import, and the CLI imports every stage.
    from imgsearch.models.captioner import MlxCaptioner

    return MlxCaptioner(config)


class CaptionStage:
    """Caption each image's derivative. Writes only through `store.text.save_caption`.

    `load_captioner` builds the captioner from config; tests pass a fake.
    """

    name = "caption"
    requires = ["ingest"]

    def __init__(
        self,
        config: Config,
        load_captioner: Callable[[CaptionerConfig], LoadedCaptioner] = _load_mlx_captioner,
    ) -> None:
        self.version = config.stages[self.name]
        self._load_captioner = load_captioner
        self._captioner: LoadedCaptioner | None = None

    def load(self, ctx: Context) -> None:
        self._captioner = self._load_captioner(ctx.config.models.captioner)

    def run(self, image: ImageRecord, ctx: Context) -> None:
        if self._captioner is None:
            raise RuntimeError("CaptionStage.run called before load")
        if image.derivative_path is None:
            raise ValueError(f"image {image.id} has no derivative")
        text = self._captioner.caption(ctx.config.derivatives_root / image.derivative_path)
        save_caption(ctx.conn, image.id, text, ctx.config.models.captioner.name)

    def unload(self) -> None:
        if self._captioner is not None:
            self._captioner.unload()
        self._captioner = None
