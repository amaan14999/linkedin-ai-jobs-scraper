from __future__ import annotations

from typing import List

from .config import AppConfig
from .linkedin_client import scrape
from .models import Job


def run_scrape(cfg: AppConfig, print_urls: bool = False) -> List[Job]:
    """
    Step 1 pipeline: scrape LinkedIn and return jobs.
    Later this will become:
      scrape -> dedupe/store in DB -> LLM filter -> persist decisions
    """
    return scrape(cfg.linkedin, print_urls=print_urls)
