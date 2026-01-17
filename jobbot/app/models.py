from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Job:
    job_url: str
    title: str
    company_name: str
    description: Optional[str] = None
