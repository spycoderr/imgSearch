"""Model implementations behind the interfaces (embedder, OCR, captioner, rewriter)."""

import logging

# Importing torch (siglip.py) logs "NOTE: Redirects are currently not supported in MacOs."
# at warning level. It only says a torch feature we don't use is missing (review N1). Set
# here because this package always loads before any module in it imports torch.
logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)
