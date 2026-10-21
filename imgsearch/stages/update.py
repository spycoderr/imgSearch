"""`imgsearch update`: import, upload, then ingest, embed, ocr, filename and places
(P1.5–P1.8).

Captions are not part of it: they run separately, in the background (decided after M7).
New originals are uploaded to S3 right after the import, before indexing; the encrypted DB
copy is uploaded last, so it includes this run's index (review R7). If the upload can't run
or fails (offline, no key, S3 not configured), indexing still runs and the result says how
many originals are not yet in S3.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from imgsearch.config import Config
from imgsearch.interfaces import Context, Stage
from imgsearch.sources.importer import ImportResult, import_inbox
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.filename import FilenameStage
from imgsearch.stages.ingest import IngestStage
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.places import PlacesStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.roots import require_mounted
from imgsearch.store.upload import UploadResult, not_in_s3

# In order; no CaptionStage.
UPDATE_STAGES = (IngestStage, EmbedStage, OcrStage, FilenameStage, PlacesStage)


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
    upload_db: Callable[[UploadResult], None],
) -> UpdateResult:
    """Check the pen drive, import, upload the originals, run `stages` in order through the
    runner, then upload the DB copy.

    `upload` uploads the originals and `upload_db` records the DB copy's outcome in the
    result (the CLI passes steps that open S3 and the Keychain; tests pass fakes). Neither
    raises on a store error. Raises DriveNotMounted before doing anything if the pen drive
    is missing.
    """
    require_mounted(config, "originals_root", "derivatives_root")
    imported = import_inbox(config, conn)
    uploaded = upload()
    pending = len(not_in_s3(conn))
    ctx = Context(config=config, conn=conn)
    stage_counts = {stage.name: run_stage(stage, ctx) for stage in stages}
    upload_db(uploaded)
    return UpdateResult(imported, uploaded, pending, stage_counts)
