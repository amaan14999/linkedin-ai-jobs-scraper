from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class LinkedInSearchConfig:
    keywords: str
    location: str

    distance: int = 25

    # optional f_WT (1/2/3). If None -> not sent
    f_WT: Optional[str] = None

    experience_levels: str = "2,3"
    easy_apply: bool = False
    company_ids: List[int] = field(default_factory=list)

    hours_old: Optional[int] = 24
    fetch_description: bool = True

    results_wanted: int = 50
    offset: int = 0

    delay_seconds: float = 2.5
    jitter_seconds: float = 1.5
    timeout_seconds: int = 12
    filter_out_companies: List[str] = field(default_factory=list)


@dataclass
class AppConfig:
    linkedin: LinkedInSearchConfig


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    linkedin_dict: Dict[str, Any] = data.get("linkedin", data)
    cfg = LinkedInSearchConfig(**linkedin_dict)

    return AppConfig(linkedin=cfg)
