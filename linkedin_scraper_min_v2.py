from __future__ import annotations

import json
import random
import re
import time
import urllib3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
import yaml
from bs4 import BeautifulSoup

# Disable SSL warnings (matches reference behavior for scraping tools)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Mimic the headers from jobspy/linkedin/constant.py
# These are crucial for LinkedIn to return full page results (25 items) instead of 10 or blocking.
DEFAULT_HEADERS = {
    "authority": "www.linkedin.com",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


@dataclass
class LinkedInSearchConfig:
    # Core search
    keywords: str
    location: str

    # Optional filters / paging
    distance: int = 25
    f_WT: Optional[int] = None
    experience_levels: str = "2,3"
    easy_apply: bool = False
    company_ids: List[int] = field(default_factory=list)

    # Recency (hours)
    hours_old: Optional[int] = 24

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

    if cfg.f_WT is not None:
        if cfg.f_WT not in (1, 2, 3):
            raise ValueError("linkedin.f_WT must be 1, 2, or 3")
        params["f_WT"] = cfg.f_WT
    if cfg.experience_levels:
        params["f_E"] = cfg.experience_levels
    if cfg.easy_apply:
        params["f_AL"] = "true"
    if cfg.company_ids:
        params["f_C"] = ",".join(str(x) for x in cfg.company_ids)
    if cfg.hours_old is not None:
        seconds_old = int(cfg.hours_old) * 3600
        params["f_TPR"] = f"r{seconds_old}"

    return params


def _sleep_polite(cfg: LinkedInSearchConfig) -> None:
    time.sleep(cfg.delay_seconds + random.random() * cfg.jitter_seconds)


def _extract_job_id_from_href(href: str) -> Optional[str]:
    href = href.split("?")[0]
    m = re.search(r"-(\d+)$", href)
    if m:
        return m.group(1)
    m2 = re.search(r"/jobs/view/(\d+)", href)
    return m2.group(1) if m2 else None


def _plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_job_description(
    session: requests.Session, job_id: str, timeout: int
) -> Optional[str]:
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    try:
        # verify=False helps with some proxy/scraping setups and matches the reference logic
        resp = session.get(url, timeout=timeout, verify=False)
        resp.raise_for_status()
    except Exception:
        return None

    if "linkedin.com/signup" in str(resp.url) or "linkedin.com/login" in str(resp.url):
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    div = soup.find("div", class_=lambda x: x and "show-more-less-html__markup" in x)
    if not div:
        return None

    return _plain_text(str(div))


def scrape_linkedin(cfg: LinkedInSearchConfig) -> List[Dict[str, Any]]:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    jobs: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    start = (cfg.offset // 10) * 10 if cfg.offset else 0

    # Continue until we have enough results or we hit a sanity limit (1000)
    while len(jobs) < cfg.results_wanted and start < 1000:
        params = build_search_params(cfg, start)
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        print(
            f"Requesting: {requests.Request('GET', url, params=params).prepare().url}"
        )
        try:
            resp = session.get(
                url, params=params, timeout=cfg.timeout_seconds, verify=False
            )
        except Exception as e:
            print(f"Request Error: {e}")
            break

        if resp.status_code == 429:
            print("429 Too Many Requests. Stopping.")
            break
        if resp.status_code >= 400:
            print(f"Error {resp.status_code}: {resp.text[:100]}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        job_cards = soup.find_all("div", class_="base-search-card")

        # If no cards returned, we reached the end
        if not job_cards:
            print("No more job cards found.")
            break

        # [CRITICAL FIX]
        # Instead of fixed 'page_step', we must track how many items
        # we actually received to calculate the next offset correctly.
        jobs_in_batch = len(job_cards)
        print(f"Fetching page starting at {start}, received {jobs_in_batch} jobs.")

        for job_card in job_cards:
            link_tag = job_card.find("a", class_="base-card__full-link")
            if not link_tag or not link_tag.get("href"):
                continue

            job_id = _extract_job_id_from_href(link_tag["href"])
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title_tag = job_card.find("span", class_="sr-only")
            title = title_tag.get_text(strip=True) if title_tag else "N/A"

            company_tag = job_card.find("h4", class_="base-search-card__subtitle")
            company_a = company_tag.find("a") if company_tag else None
            company = company_a.get_text(strip=True) if company_a else "N/A"

            job_url = f"https://www.linkedin.com/jobs/view/{job_id}"

            description = None
            if cfg.fetch_description:
                description = fetch_job_description(
                    session, job_id, cfg.timeout_seconds
                )

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

        # [CRITICAL FIX]
        # Increment start by the actual number of jobs received.
        # If LinkedIn sends 25, we skip 25. If 10, we skip 10.
        start += jobs_in_batch

        if len(jobs) < cfg.results_wanted:
            _sleep_polite(cfg)

    return jobs


def load_config(path: str) -> LinkedInSearchConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cfg_dict = data.get("linkedin", data)
    return LinkedInSearchConfig(**cfg_dict)


def main() -> None:
    # Mimics arg parsing from your snippet
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out", default="-")
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
