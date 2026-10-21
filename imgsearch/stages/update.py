"""`imgsearch update`: import, upload, then ingest, embed and ocr (plan P1.5, P1.6).

Captions are not part of it: they run separately, in the background (decided after M7).
New originals are uploaded to S3 right after the import, before indexing. If the upload
can't run (offline, no key), indexing still runs and the result says how many originals
are not yet in S3.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from imgsearch.config import Config
from imgsearch.interfaces import Context, Stage
from imgsearch.sources.importer import ImportResult, import_inbox
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.ingest import IngestStage
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.roots import require_mounted
from imgsearch.store.upload import UploadResult, not_in_s3

UPDATE_STAGES = (IngestStage, EmbedStage, OcrStage)  # in this order; never CaptionStage


@dataclass
class UpdateResult:
    imported: ImportResult
    uploaded: UploadResult
    not_in_s3: int  # originals without a verified S3 copy after the upload step
    stages: dict[str, dict[str, int]]  # stage name -> {"done": n, "failed": m}


def update(
    config: Config,
    conn: sqlite3.Connection,
    stages: list[Stage],
    upload: Callable[[], UploadResult],
) -> UpdateResult:
    """Check the pen drive, import, upload, then run `stages` in order through the runner.

    `upload` runs the upload step (the CLI passes one that opens S3 and the Keychain; tests
    pass fakes). Raises DriveNotMounted before doing anything if the pen drive is missing.
    """
    require_mounted(config, "originals_root", "derivatives_root")
    imported = import_inbox(config, conn)
    uploaded = upload()
    pending = len(not_in_s3(conn))
    ctx = Context(config=config, conn=conn)
    stage_counts = {stage.name: run_stage(stage, ctx) for stage in stages}
    return UpdateResult(imported, uploaded, pending, stage_counts)
