from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import yaml
from bs4 import BeautifulSoup


DEFAULT_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "en-US,en;q=0.9",
}


@dataclass
class LinkedInSearchConfig:
    # Core search
    keywords: str
    location: str

    # Optional filters / paging
    distance: int = 25
    is_remote: bool = False               # adds f_WT=2
    experience_levels: str = "2,3"        # LinkedIn uses comma-separated values (e.g., 1,2,3,4,5,6)
    easy_apply: bool = False              # adds f_AL=true
    company_ids: List[int] = field(default_factory=list)  # adds f_C=... (comma-separated)

    # Recency (hours)
    hours_old: Optional[int] = 24         # adds f_TPR=r<seconds> (e.g., r86400)

    # Fetch details
    fetch_description: bool = True

    # Output controls
    results_wanted: int = 50
    offset: int = 0

    # Request tuning
    delay_seconds: float = 2.5
    jitter_seconds: float = 1.5
    timeout_seconds: int = 12


def build_search_params(cfg: LinkedInSearchConfig, start: int) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "keywords": cfg.keywords,
        "location": cfg.location,
        "distance": cfg.distance,
        "pageNum": 0,
        "start": start,
    }

    # Remote filter
    if cfg.is_remote:
        params["f_WT"] = 2

    # Experience levels
    if cfg.experience_levels:
        params["f_E"] = cfg.experience_levels

    # Easy apply
    if cfg.easy_apply:
        params["f_AL"] = "true"

    # Company IDs
    if cfg.company_ids:
        params["f_C"] = ",".join(str(x) for x in cfg.company_ids)

    # Recency
    if cfg.hours_old is not None:
        seconds_old = int(cfg.hours_old) * 3600
        params["f_TPR"] = f"r{seconds_old}"

    return params


def _sleep_polite(cfg: LinkedInSearchConfig) -> None:
    time.sleep(cfg.delay_seconds + random.random() * cfg.jitter_seconds)


def _extract_job_id_from_href(href: str) -> Optional[str]:
    # Example href: https://www.linkedin.com/jobs/view/software-engineer-1234567890?...
    href = href.split("?")[0]
    m = re.search(r"-(\d+)$", href)
    if m:
        return m.group(1)
    # Sometimes it might be /jobs/view/<id>/
    m2 = re.search(r"/jobs/view/(\d+)", href)
    return m2.group(1) if m2 else None


def _plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_job_description(session: requests.Session, job_id: str, timeout: int) -> Optional[str]:
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return None

    # If LinkedIn redirects to signup/login, the description won't be accessible.
    if "linkedin.com/signup" in str(resp.url) or "linkedin.com/login" in str(resp.url):
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Their scraper looks for a div with class containing "show-more-less-html__markup"
    div = soup.find("div", class_=lambda x: x and "show-more-less-html__markup" in x)
    if not div:
        return None

    return _plain_text(str(div))


def scrape_linkedin(cfg: LinkedInSearchConfig) -> List[Dict[str, Any]]:
    """Returns a list of dicts with fields: job_url, title, company_name, description."""

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    jobs: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    start = (cfg.offset // 25) * 25 if cfg.offset else 0

    while len(jobs) < cfg.results_wanted and start < 1000:
        params = build_search_params(cfg, start)

        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        try:
            resp = session.get(url, params=params, timeout=cfg.timeout_seconds)
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

        if resp.status_code == 429:
            raise RuntimeError("429 Too Many Requests (rate-limited by LinkedIn). Slow down / add backoff.")
        if resp.status_code >= 400:
            raise RuntimeError(f"LinkedIn returned HTTP {resp.status_code}: {resp.text[:200]}")

        soup = BeautifulSoup(resp.text, "html.parser")
        job_cards = soup.find_all("div", class_="base-search-card")
        if not job_cards:
            break

        for job_card in job_cards:
            link_tag = job_card.find("a", class_="base-card__full-link")
            if not link_tag or not link_tag.get("href"):
                continue

            job_id = _extract_job_id_from_href(link_tag["href"])
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            # Title
            title_tag = job_card.find("span", class_="sr-only")
            title = title_tag.get_text(strip=True) if title_tag else "N/A"

            # Company
            company_tag = job_card.find("h4", class_="base-search-card__subtitle")
            company_a = company_tag.find("a") if company_tag else None
            company = company_a.get_text(strip=True) if company_a else "N/A"

            job_url = f"https://www.linkedin.com/jobs/view/{job_id}"

            description = None
            if cfg.fetch_description:
                description = fetch_job_description(session, job_id, cfg.timeout_seconds)

            jobs.append(
                {
                    "job_url": job_url,
                    "title": title,
                    "company_name": company,
                    "description": description,
                }
            )

            if len(jobs) >= cfg.results_wanted:
                break

        start += len(job_cards)
        if len(jobs) < cfg.results_wanted:
            _sleep_polite(cfg)

    return jobs


def load_config(path: str) -> LinkedInSearchConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Support nesting under "linkedin"
    cfg_dict = data.get("linkedin", data)
    return LinkedInSearchConfig(**cfg_dict)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scrape LinkedIn job listings (guest endpoint) and output JSON")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--out", default="-", help="Output path (default stdout). Use '-' for stdout")

    args = parser.parse_args()
    cfg = load_config(args.config)

    jobs = scrape_linkedin(cfg)
    out_json = json.dumps(jobs, ensure_ascii=False, indent=2)

    if args.out == "-":
        print(out_json)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_json)
        print(f"Wrote {len(jobs)} jobs to {args.out}")


if __name__ == "__main__":
    main()
