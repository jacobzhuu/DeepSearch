CURRENT_TASK_STATUS_VALUES = (
    "PLANNED",
    "PAUSED",
    "CANCELLED",
)

FUTURE_RUNTIME_STATUS_VALUES = (
    "QUEUED",
    "RUNNING",
    "FAILED",
    "COMPLETED",
    "NEEDS_REVISION",
)

TASK_STATE_VALUES = (
    "PLANNED",
    "QUEUED",
    "RUNNING",
    "SEARCHING",
    "ACQUIRING",
    "PARSING",
    "INDEXING",
    "DRAFTING_CLAIMS",
    "VERIFYING",
    "RESEARCHING_MORE",
    "REPORTING",
    "COMPLETED",
    "FAILED",
    "PAUSED",
    "CANCELLED",
    "NEEDS_REVISION",
)


def sql_in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
