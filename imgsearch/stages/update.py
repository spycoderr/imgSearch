"""`imgsearch update`: import the inboxes, then run ingest, embed and ocr (plan P1.5).

Captions are not part of it: they run separately, in the background (decided after M7).
"""

import sqlite3
from dataclasses import dataclass

from imgsearch.config import Config
from imgsearch.interfaces import Context, Stage
from imgsearch.sources.importer import ImportResult, import_inbox
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.ingest import IngestStage
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.roots import require_mounted

UPDATE_STAGES = (IngestStage, EmbedStage, OcrStage)  # in this order; never CaptionStage


@dataclass
class UpdateResult:
    imported: ImportResult
    stages: dict[str, dict[str, int]]  # stage name -> {"done": n, "failed": m}


def update(config: Config, conn: sqlite3.Connection, stages: list[Stage]) -> UpdateResult:
    """Check the pen drive, import, then run `stages` in order through the runner.

    Raises DriveNotMounted before doing anything if the pen drive is missing.
    """
    require_mounted(config, "originals_root", "derivatives_root")
    imported = import_inbox(config, conn)
    ctx = Context(config=config, conn=conn)
    return UpdateResult(imported, {stage.name: run_stage(stage, ctx) for stage in stages})
