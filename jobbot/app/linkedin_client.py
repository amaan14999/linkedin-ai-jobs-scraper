from __future__ import annotations

import random
import re
import time
import urllib3
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .config import LinkedInSearchConfig
from .models import Job

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "authority": "www.linkedin.com",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


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


def build_search_params(cfg: LinkedInSearchConfig, start: int) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "keywords": cfg.keywords,
        "location": cfg.location,
        "distance": cfg.distance,
        "pageNum": 0,
        "start": start,
    }

    # Optional f_WT: 1/2/3 (only send if set)
    if cfg.f_WT is not None:
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


def fetch_job_description(
    session: requests.Session, job_id: str, timeout: int
) -> Optional[str]:
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    try:
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


def scrape(cfg: LinkedInSearchConfig, print_urls: bool = False) -> List[Job]:
    """
    Scrape LinkedIn job cards via the public guest endpoint and optionally fetch job descriptions.

    Returns a list[Job] with:
      - job_url
      - title
      - company_name
      - description (optional)
    """
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    jobs: List[Job] = []
    seen_ids: set[str] = set()

    start = (cfg.offset // 10) * 10 if cfg.offset else 0

    while len(jobs) < cfg.results_wanted and start < 1000:
        params = build_search_params(cfg, start)

        try:
            resp = session.get(
                SEARCH_URL, params=params, timeout=cfg.timeout_seconds, verify=False
            )
        except Exception as e:
            print(f"Request Error: {e}")
            break

        if resp.status_code == 429:
            print("429 Too Many Requests. Stopping.")
            break
        if resp.status_code >= 400:
            print(f"Error {resp.status_code}: {resp.text[:200]}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        job_cards = soup.find_all("div", class_="base-search-card")

        if not job_cards:
            print("No more job cards found. Stopping.")
            break

        jobs_in_batch = len(job_cards)

        if print_urls:
            req_url = requests.Request("GET", SEARCH_URL, params=params).prepare().url
            print(req_url)
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

            if cfg.filter_out_companies:
                comp_lower = company.lower()
                blocked = any(
                    block.lower().strip() in comp_lower
                    for block in cfg.filter_out_companies
                    if block
                )
                if blocked:
                    continue
            job_url = f"https://www.linkedin.com/jobs/view/{job_id}"

            description = None
            if cfg.fetch_description:
                description = fetch_job_description(
                    session, job_id, cfg.timeout_seconds
                )

            jobs.append(
                Job(
                    job_url=job_url,
                    title=title,
                    company_name=company,
                    description=description,
                )
            )

            if len(jobs) >= cfg.results_wanted:
                break

        # IMPORTANT: use actual batch size (LinkedIn can return 10/25)
        start += jobs_in_batch

        if len(jobs) < cfg.results_wanted:
            _sleep_polite(cfg)

    return jobs
