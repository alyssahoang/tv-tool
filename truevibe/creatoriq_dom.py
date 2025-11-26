from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Set

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PROFILE_CARD_SELECTOR = ".HeightPreservingItem-module__root"
PROFILE_NAME_SELECTOR = '.MuiStack-root.css-bxytpu [data-testid="creator-fullname"]'
PROFILE_HANDLE_SELECTOR = '.MuiStack-root.css-bxytpu [data-testid="creator-handle"]'
PROFILE_AVATAR_SELECTOR = '.MuiStack-root.css-bxytpu [data-testid="creator-avatar"] img'
PROFILE_BIO_SELECTOR = '[data-testid="creator-bio"]'
PROFILE_PLATFORM_STACK_SELECTOR = ".MuiStack-root.css-18zsr3k"
PROFILE_PLATFORM_ICON_SELECTOR = ".ciq-icon"
PROFILE_FOLLOWER_SELECTOR = ".MuiTypography-body-lg"
SPOTLIGHT_SECTION_SELECTOR = "h4.MuiTypography-root.MuiTypography-h4.css-1js8jer"

SEARCH_ICON_SELECTOR = ".ciq-icon.ciq-search.md"
SEARCH_INPUT_SELECTOR = 'input[placeholder="Search by name or @account..."]'
PROFILE_RESULT_CARD_SELECTOR = ".CreatorCard-module__root.MuiBox-root.css-75qv9u"
PROFILE_DETAILS_ROOT_SELECTOR = '[data-testid="creator-details-sidebar-root"]'


PLATFORM_CLASS_MAP = {
    "ciq-instagram-logo": "Instagram",
    "ciq-tiktok-logo": "TikTok",
    "ciq-youtube-logo": "YouTube",
}


def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=options)
    return driver


def scroll_page(driver: webdriver.Chrome, scroll_pause_time: float = 1, scroll_step: int = 300) -> bool:
    """
    Scroll the page gradually until new content loads. Returns True when the bottom of the page was reached.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollBy(0, arguments[0]);", scroll_step)
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            print("Reached the bottom of the page.")
            return True
        last_height = new_height
        try:
            spotlight_section = driver.find_element(By.CSS_SELECTOR, SPOTLIGHT_SECTION_SELECTOR)
            if spotlight_section.is_displayed():
                print("Continue to scroll.")
                break
        except Exception:
            pass
    return False


def wait_for_profile_growth(
    driver: webdriver.Chrome,
    previous_count: int,
    max_attempts: int = 5,
    delay: float = 1.5,
) -> bool:
    """
    After scrolling, wait for new cards to render. Returns True if additional profiles appear.
    """
    for _ in range(max_attempts):
        time.sleep(delay)
        current_count = len(driver.find_elements(By.CSS_SELECTOR, PROFILE_CARD_SELECTOR))
        if current_count > previous_count:
            return True
    return False


def get_platform_from_icon(platform_class: str) -> str:
    for class_name, platform in PLATFORM_CLASS_MAP.items():
        if class_name in platform_class:
            return platform
    return "Unknown"


def parse_follower_count(raw_value: str) -> Optional[int]:
    if not raw_value:
        return None
    normalized = raw_value.replace(",", "").strip()
    match = re.match(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", normalized, re.IGNORECASE)
    if not match:
        digits = re.sub(r"\D", "", normalized)
        return int(digits) if digits else None
    number = float(match.group(1))
    suffix = match.group(2).upper()
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


class CreatorIQDomScraper:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def scrape_report(
        self,
        url: str,
        max_profiles: int = 100,
        detail_limit: Optional[int] = None,
    ) -> List[Dict[str, object]]:
        driver = build_driver(headless=self.headless)
        try:
            profiles = self._scrape_profiles(driver, url, max_profiles=max_profiles)
        finally:
            driver.quit()
        if not profiles:
            return []
        limit = len(profiles) if detail_limit is None else min(len(profiles), detail_limit)
        if limit > 0:
            detail_driver = build_driver(headless=self.headless)
            try:
                self._attach_profile_details(detail_driver, url, profiles, limit)
            finally:
                detail_driver.quit()
        return profiles

    def _scrape_profiles(
        self,
        driver: webdriver.Chrome,
        url: str,
        max_profiles: int,
    ) -> List[Dict[str, object]]:
        driver.get(url)
        time.sleep(10)
        profiles_data: List[Dict[str, object]] = []
        unique_handles: Set[str] = set()
        total_scraped = 0
        reached_bottom_last = False

        while total_scraped < max_profiles:
            profile_elements = driver.find_elements(By.CSS_SELECTOR, PROFILE_CARD_SELECTOR)
            visible_count = len(profile_elements)
            print(f"Found {visible_count} profiles on this page")
            for element in profile_elements:
                data = self._extract_profile_card(element)
                if not data:
                    continue
                handle = data.get("Handle")
                if not handle or handle in unique_handles:
                    if handle:
                        print(f"Skipping duplicate profile with handle: {handle}")
                    continue
                profiles_data.append(data)
                unique_handles.add(handle)
                total_scraped += 1
                print(f"Total unique profiles found so far: {total_scraped}")
                if total_scraped >= max_profiles:
                    break
            if total_scraped >= max_profiles:
                break
            reached_bottom = scroll_page(driver)
            if reached_bottom:
                reached_bottom_last = True
                break
            if not wait_for_profile_growth(driver, visible_count):
                print("No additional profiles loaded after waiting. Assuming end of list.")
                reached_bottom_last = True
                break

        if reached_bottom_last:
            print(f"Scraping complete at end of feed. Total unique profiles found: {total_scraped}")
        else:
            print(f"Scraping complete. Total unique profiles found: {total_scraped}")
        return profiles_data

    def _extract_profile_card(self, profile_element) -> Optional[Dict[str, str]]:
        try:
            fullname = profile_element.find_element(By.CSS_SELECTOR, PROFILE_NAME_SELECTOR).text
            handle = profile_element.find_element(By.CSS_SELECTOR, PROFILE_HANDLE_SELECTOR).text
            image_url = profile_element.find_element(By.CSS_SELECTOR, PROFILE_AVATAR_SELECTOR).get_attribute("src")
        except Exception:
            return None
        try:
            platform_element = profile_element.find_element(By.CSS_SELECTOR, PROFILE_PLATFORM_STACK_SELECTOR)
            platform_icon_class = platform_element.find_element(By.CSS_SELECTOR, PROFILE_PLATFORM_ICON_SELECTOR).get_attribute("class")
            platform = get_platform_from_icon(platform_icon_class)
            followers = platform_element.find_element(By.CSS_SELECTOR, PROFILE_FOLLOWER_SELECTOR).text
        except Exception:
            platform = "Unknown"
            followers = ""
        try:
            bio = profile_element.find_element(By.CSS_SELECTOR, PROFILE_BIO_SELECTOR).text
        except Exception:
            bio = "N/A"
        return {
            "Full Name": fullname,
            "Handle": handle,
            "Image URL": image_url,
            "Platform": platform,
            "Followers": followers,
            "Bio": bio,
        }

    def _attach_profile_details(
        self,
        driver: webdriver.Chrome,
        url: str,
        profiles: Sequence[Dict[str, object]],
        max_profiles: int,
    ) -> None:
        for profile in profiles[:max_profiles]:
            handle = str(profile.get("Handle") or "").strip()
            if not handle:
                continue
            print(f"Processing profile: {handle}")
            success = self.visit_and_search(driver, url, handle)
            if not success:
                continue
            details = self.scrape_profile_details(driver)
            profile["Details"] = details

    def visit_and_search(self, driver: webdriver.Chrome, url: str, handle: str) -> bool:
        driver.get(url)
        time.sleep(5)
        clean_handle = handle.lstrip("@")
        try:
            search_box = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, SEARCH_ICON_SELECTOR))
            )
            search_box.click()
            search_input = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, SEARCH_INPUT_SELECTOR))
            )
            search_input.clear()
            search_input.send_keys(clean_handle)
            time.sleep(1)
            search_input.send_keys(Keys.RETURN)
            profile_card = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, PROFILE_RESULT_CARD_SELECTOR))
            )
            profile_card.click()
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, PROFILE_DETAILS_ROOT_SELECTOR))
            )
            return True
        except Exception as exc:
            print(f"Error during search and click for {handle}: {exc}")
            return False

    def scrape_profile_details(self, driver: webdriver.Chrome) -> Dict[str, object]:
        details: Dict[str, object] = {}
        details["About"] = self._safe_text(
            driver,
            f"{PROFILE_DETAILS_ROOT_SELECTOR} .MuiTypography-root.MuiTypography-title-md",
        )
        tags_elements = driver.find_elements(By.CSS_SELECTOR, ".MuiTypography-root.MuiTypography-body-md.css-12t7p4b")
        if tags_elements:
            details["Tags"] = tags_elements[0].text or "N/A"
            details["Category"] = tags_elements[1].text if len(tags_elements) > 1 else "N/A"
        else:
            details["Tags"] = "N/A"
            details["Category"] = "N/A"

        social_links: List[str] = []
        try:
            social_accounts = driver.find_elements(By.CSS_SELECTOR, ".MuiChip-action")
            for account in social_accounts:
                link = account.get_attribute("href")
                if link:
                    social_links.append(link)
        except Exception as exc:
            print(f"Error scraping Social Accounts: {exc}")
        details["Social Links"] = social_links

        follower_counts = driver.find_elements(By.CSS_SELECTOR, ".MuiTypography-root.MuiTypography-h4.css-rgovxk")
        if follower_counts:
            details["Instagram Followers"] = follower_counts[0].text if len(follower_counts) > 0 else "N/A"
            details["TikTok Followers"] = follower_counts[1].text if len(follower_counts) > 1 else "N/A"
        engagement_sections = driver.find_elements(By.CSS_SELECTOR, ".MuiStack-root.css-1qqjprm")
        if engagement_sections:
            details["Instagram Engagement Rate"] = engagement_sections[0].text if len(engagement_sections) > 0 else "N/A"
            details["TikTok Engagement Rate"] = engagement_sections[1].text if len(engagement_sections) > 1 else "N/A"

        content_spotlight: List[Dict[str, str]] = []
        try:
            content_images = driver.find_elements(By.CSS_SELECTOR, 'img[src^="https://static-resources.creatoriq.com/social-pictures"]')
            content_links = driver.find_elements(By.CSS_SELECTOR, 'a[data-testid="post-card"]')
            for idx, image in enumerate(content_images[: min(3, len(content_images), len(content_links))]):
                img_url = image.get_attribute("src")
                post_url = content_links[idx].get_attribute("href")
                content_spotlight.append({"Image URL": img_url, "Post URL": post_url})
        except Exception as exc:
            print(f"Error scraping content spotlight: {exc}")
        details["Top Content"] = content_spotlight

        details["Female Audience"] = self._safe_text(driver, '[data-testid="pie-chart-Female-value"]')
        details["Male Audience"] = self._safe_text(driver, '[data-testid="pie-chart-Male-value"]')

        age_groups = ["<18", "18-24", "25-34", "35-44", "45-64"]
        age_demographics: Dict[str, str] = {}
        for group in age_groups:
            selector = f'[data-testid="bar-chart-right-label-{group}"]'
            age_demographics[group] = self._safe_text(driver, selector)
        details["Age Demographics"] = age_demographics

        return details

    def _safe_text(self, driver: webdriver.Chrome, selector: str) -> str:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            return element.text or "N/A"
        except Exception:
            return "N/A"


def normalize_handle(handle: str) -> str:
    return handle.lstrip("@").strip().lower()


def normalize_dom_profile(profile: Dict[str, object]) -> Dict[str, object]:
    handle = normalize_handle(str(profile.get("Handle") or ""))
    follower_text = str(profile.get("Followers") or "")
    follower_count = parse_follower_count(follower_text)
    demographics = {
        "bio": profile.get("Bio"),
        "image_url": profile.get("Image URL"),
        "details": profile.get("Details"),
    }
    return {
        "name": profile.get("Full Name") or handle or "Unknown Creator",
        "handle": handle or "unknown",
        "platform": profile.get("Platform") or "Unknown",
        "follower_count": follower_count,
        "demographics": demographics,
    }
