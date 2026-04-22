TASK_STATE_VALUES = (
    "PLANNED",
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
