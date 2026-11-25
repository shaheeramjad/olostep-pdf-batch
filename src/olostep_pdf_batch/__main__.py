"""Allow ``python -m olostep_pdf_batch``."""

import sys

from .cli import main

sys.exit(main())
