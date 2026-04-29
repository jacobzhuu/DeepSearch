#!/usr/bin/env python3
from __future__ import annotations

from services.orchestrator.app.services.pipeline_worker import run_worker_forever

if __name__ == "__main__":
    run_worker_forever()
