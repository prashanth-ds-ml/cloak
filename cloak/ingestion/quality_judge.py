# Moved to cloak.quality.quality_judge — this stub keeps old imports working.
from cloak.quality.quality_judge import *  # noqa: F401, F403
from cloak.quality.quality_judge import (  # noqa: F401
    PageScore, judge, aggregate_page_results,
)
# Alias for code that still references the old name
JudgeResult = PageScore  # noqa: F401
