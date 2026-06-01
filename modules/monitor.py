"""
Content Monitor & Scraper Module
Monitors all configured sources (RSS feeds, websites, Wix blogs)
and scrapes newly published content into the knowledge base.
"""

import feedparser
import requests
import logging
import time
import re
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from . import knowledge_base as kb

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------
# MAIN MONITOR FUNCTION
# ----------------------------------------------------------------

def run_monitoring_cycle(config: Dict, max_per_source: int = 5) -> Dict:
    """
    Run one complete monitoring cycle across all enabled sources.
    Returns summary of what was found.
    """
    sources = kb.get_all_sources(enabled_only=True)
    if not sources:
        logger.warning("No enabled sources found. Add sources to config/sources.yaml")
        return {'sources_checked': 0, 'new_articles': 0}

    summary = {
        'sources_checked': 0,
        'sources_successful': 0,
        'sources_failed': 0,
        'new_articles': 0,
        'errors': []
    }

    logger.info(f"Starting monitoring cycle for {len(sources)} source(s)")

    for source in sorted(sources, key=lambda x: x.get('priority', 3)):
        try:
            source_type = source.get('type', 'webpage')
            logger.info(f"Checking source: {source['name']} ({source_type})")

            articles = []
            if source_type == 'rss':
                articles = scrape_rss_feed(source, config)
            elif source_type == 'wix_blog':
                articles = scrape_wix_blog(source, config)
            elif source_type in ('webpage', 'news', 'blog'):
                articles = scrape_webpage(source, config)
            else:
                articles = scrape_webpage(source, config)

            new_count = 0
            for article in articles[:max_per_source]:
                if not kb.url_already_processed(article['url']):
                    article_id = kb.save_article(article)
                    if article_id:
                        new_count += 1
                        summary['new_articles'] += 1

            kb.update_source_last_checked(source['id'], found_new=(new_count > 0))
            summary['sources_checked'] += 1
            summary['sources_successful'] += 1

            if new_count > 0:
                logger.info(f"  → Found {new_count} new article(s) from {source['name']}")
            else:
                logger.info(f"  → No new content from {source['name']}")

        except Exception as e:
            logger.error(f"Error monitoring source '{source['name']}': {e}")
            summary['sources_failed'] += 1
            summary['errors'].append({'source': source['name'], 'error': str(e)})
            kb.log_to_db('ERROR', 'monitor', f"Failed to monitor source: {source['name']}", {'error': str(e)})
            time.sleep(2)

    logger.info(
        f"Monitoring cycle complete: "
        f"{summary['sources_successful']}/{summary['sources_checked']} sources OK, "
        f"{summary['new_articles']} new articles"
    )
    return summary


# ----------------------------------------------------------------
# RSS FEED SCRAPER
# ----------------------------------------------------------------

def scrape_rss_feed(source: Dict, config: Dict) -> List[Dict]:
    """Scrape articles from an RSS/Atom feed."""
    articles = []
    timeout = config.get('monitoring', {}).get('request_timeout_seconds', 15)

    try:
        feed = feedparser.parse(source['url'], request_headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ContentBot/1.0)'
        })

        if feed.bozo and not feed.entries:
            logger.warning(f"RSS parse warning for {source['url']}: {feed.bozo_exception}")
            return []

        for entry in feed.entries:
            try:
                url = entry.get('link', '')
                if not url or kb.url_already_processed(url):
                    continue

                # Extract publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6]).isoformat()
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    pub_date = datetime(*entry.updated_parsed[:6]).isoformat()

                # Extract content
                content = ''
                if hasattr(entry, 'content') and entry.content:
                    content = entry.content[0].get('value', '')
                elif hasattr(entry, 'summary'):
                    content = entry.summary

                # Clean HTML from content
                content = clean_html(content)

                # Extract images
                images = extract_images_from_html(
                    entry.content[0].get('value', '') if hasattr(entry, 'content') and entry.content else ''
                )

                # Extract tags
                tags = [t.term for t in entry.get('tags', [])] if hasattr(entry, 'tags') else []
                tags.extend(source.get('tags', []) if isinstance(source.get('tags'), list) else [])

                article = {
                    'source_id': source['id'],
                    'title': entry.get('title', 'Untitled').strip(),
                    'url': url,
                    'content': content,
                    'images': images,
                    'tags': tags,
                    'author': entry.get('author', ''),
                    'published_at': pub_date,
                    'category': source.get('category', ''),
                    'source_website': urlparse(source['url']).netloc,
                    'word_count': len(content.split()) if content else 0
                }
                articles.append(article)

            except Exception as e:
                logger.debug(f"Error processing RSS entry: {e}")
                continue

    except Exception as e:
        logger.error(f"Failed to fetch RSS feed {source['url']}: {e}")

    return articles


# ----------------------------------------------------------------
# WIX BLOG SCRAPER
# ----------------------------------------------------------------

def scrape_wix_blog(source: Dict, config: Dict) -> List[Dict]:
    """Scrape articles from a Wix blog (tries RSS first, then HTML)."""
    # Try RSS first - Wix blogs often have /feed.xml or /blog/feed.xml
    rss_urls = [
        source['url'].rstrip('/') + '/feed.xml',
        source['url'].rstrip('/') + '/blog/feed.xml',
        source['url'].rstrip('/') + '/rss',
    ]

    for rss_url in rss_urls:
        try:
            feed = feedparser.parse(rss_url)
            if feed.entries:
                logger.debug(f"Wix blog RSS found at: {rss_url}")
                rss_source = dict(source)
                rss_source['url'] = rss_url
                rss_source['type'] = 'rss'
                return scrape_rss_feed(rss_source, config)
        except Exception:
            continue

    # Fall back to HTML scraping
    return scrape_webpage(source, config)


# ----------------------------------------------------------------
# GENERIC WEBPAGE SCRAPER
# ----------------------------------------------------------------

def scrape_webpage(source: Dict, config: Dict) -> List[Dict]:
    """
    Scrape article links from a webpage (blog index, news site, etc.)
    then fetch each article's full content.
    """
    articles = []
    timeout = config.get('monitoring', {}).get('request_timeout_seconds', 15)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        resp = fetch_with_retry(source['url'], headers=headers, timeout=timeout, config=config)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Find article links on the page
        article_links = find_article_links(soup, source['url'])

        for link_url, link_title in article_links[:15]:  # Check up to 15 links
            try:
                if kb.url_already_processed(link_url):
                    continue

                # Fetch the article page
                article_resp = fetch_with_retry(link_url, headers=headers, timeout=timeout, config=config)
                if not article_resp:
                    continue

                article_soup = BeautifulSoup(article_resp.text, 'html.parser')
                article_data = extract_article_data(article_soup, link_url, link_title)

                if article_data and len(article_data.get('content', '')) > 100:
                    article_data.update({
                        'source_id': source['id'],
                        'category': source.get('category', ''),
                        'source_website': urlparse(link_url).netloc,
                        'tags': list(set(
                            article_data.get('tags', []) +
                            (source.get('tags', []) if isinstance(source.get('tags'), list) else [])
                        ))
                    })
                    articles.append(article_data)

                time.sleep(1)  # Be polite to servers

            except Exception as e:
                logger.debug(f"Error scraping article {link_url}: {e}")
                continue

    except Exception as e:
        logger.error(f"Failed to scrape webpage {source['url']}: {e}")

    return articles


def find_article_links(soup: BeautifulSoup, base_url: str) -> List[tuple]:
    """Find article links on a blog/news index page."""
    links = []
    seen_urls = set()
    base_domain = urlparse(base_url).netloc

    # Common article container selectors (covers most blog/news sites)
    article_selectors = [
        'article a', 'h1 a', 'h2 a', 'h3 a',
        '.post a', '.blog-post a', '.entry a',
        '.article a', '.news-item a', '.post-title a',
        '[class*="post"] h2 a', '[class*="article"] a',
        '[class*="blog"] a', '.item a'
    ]

    for selector in article_selectors:
        for a_tag in soup.select(selector):
            href = a_tag.get('href', '')
            if not href:
                continue

            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Only follow links on the same domain
            if parsed.netloc != base_domain:
                continue

            # Skip navigation, tag, category links
            path = parsed.path.lower()
            skip_patterns = ['/tag/', '/category/', '/author/', '/page/', '/search',
                             '#', 'javascript:', 'mailto:', '/feed', '.xml', '.rss']
            if any(p in path for p in skip_patterns):
                continue

            # Skip very short paths (likely homepage/section links)
            if len(path) < 5 or path == '/':
                continue

            if full_url not in seen_urls:
                seen_urls.add(full_url)
                title = a_tag.get_text(strip=True)
                if len(title) > 5:  # Must have some title text
                    links.append((full_url, title))

    return links[:20]


def extract_article_data(soup: BeautifulSoup, url: str, fallback_title: str = '') -> Optional[Dict]:
    """Extract article data from a full article page."""

    # --- Title ---
    title = ''
    for sel in ['h1', '.post-title', '.entry-title', '.article-title', 'title']:
        elem = soup.select_one(sel)
        if elem:
            title = elem.get_text(strip=True)
            if len(title) > 5:
                break
    if not title:
        title = fallback_title or 'Untitled'

    # Clean title (remove site name suffix)
    if ' | ' in title:
        title = title.split(' | ')[0].strip()
    if ' - ' in title and len(title) > 60:
        title = title.split(' - ')[0].strip()

    # --- Content ---
    content = ''
    content_selectors = [
        'article', '.post-content', '.entry-content', '.article-content',
        '.blog-content', '.content-body', 'main', '.post-body',
        '[class*="content"]', '[itemprop="articleBody"]'
    ]
    for sel in content_selectors:
        elem = soup.select_one(sel)
        if elem:
            # Remove navigation, ads, sidebars
            for remove_sel in ['nav', 'aside', '.sidebar', '.advertisement',
                               '.related-posts', '.comments', 'footer', '.share',
                               'script', 'style', '.cookie', '.popup']:
                for bad in elem.select(remove_sel):
                    bad.decompose()
            content = elem.get_text(separator='\n', strip=True)
            if len(content) > 200:
                break

    if not content:
        # Last resort: get all paragraph text
        paragraphs = soup.find_all('p')
        content = '\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)

    content = clean_text(content)

    # --- Author ---
    author = ''
    for sel in ['.author', '.byline', '[rel="author"]', '[class*="author"]',
                '[itemprop="author"]', '.post-author']:
        elem = soup.select_one(sel)
        if elem:
            author = elem.get_text(strip=True)[:100]
            break

    # --- Published Date ---
    pub_date = None
    date_selectors = [
        'time[datetime]', '[itemprop="datePublished"]',
        '.post-date', '.published', '.entry-date', '.date'
    ]
    for sel in date_selectors:
        elem = soup.select_one(sel)
        if elem:
            dt_str = elem.get('datetime', '') or elem.get_text(strip=True)
            pub_date = parse_date(dt_str)
            if pub_date:
                break

    # --- Images ---
    images = []
    for img in soup.find_all('img', src=True)[:10]:
        src = urljoin(url, img['src'])
        if src.startswith('http') and not any(
            skip in src for skip in ['icon', 'logo', 'avatar', 'pixel', 'tracking', '1x1']
        ):
            images.append(src)

    # --- Meta tags for tags/keywords ---
    tags = []
    meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
    if meta_keywords and meta_keywords.get('content'):
        tags = [t.strip() for t in meta_keywords['content'].split(',')][:10]

    # Check OG tags for description
    og_desc = soup.find('meta', attrs={'property': 'og:description'})

    return {
        'title': title[:500],
        'url': url,
        'content': content[:50000],  # Cap content length
        'images': images,
        'tags': tags,
        'author': author,
        'published_at': pub_date,
        'word_count': len(content.split()) if content else 0
    }


# ----------------------------------------------------------------
# UTILITY FUNCTIONS
# ----------------------------------------------------------------

def fetch_with_retry(url: str, headers: Dict, timeout: int, config: Dict) -> Optional[requests.Response]:
    """Fetch a URL with retry logic."""
    attempts = config.get('monitoring', {}).get('retry_attempts', 3)
    delay = config.get('monitoring', {}).get('retry_delay_seconds', 5)

    for attempt in range(attempts):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            if e.response.status_code in (404, 410):
                return None  # Don't retry 404s
            logger.debug(f"HTTP error {e.response.status_code} for {url} (attempt {attempt+1})")
        except requests.RequestException as e:
            logger.debug(f"Request error for {url} (attempt {attempt+1}): {e}")

        if attempt < attempts - 1:
            time.sleep(delay)

    return None


def clean_html(html_text: str) -> str:
    """Strip HTML tags and return clean text."""
    if not html_text:
        return ''
    soup = BeautifulSoup(html_text, 'html.parser')
    return clean_text(soup.get_text(separator='\n', strip=True))


def clean_text(text: str) -> str:
    """Clean up text: remove extra whitespace, normalize newlines."""
    if not text:
        return ''
    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\t+', ' ', text)
    return text.strip()


def extract_images_from_html(html_text: str) -> List[str]:
    """Extract image URLs from HTML content."""
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, 'html.parser')
    images = []
    for img in soup.find_all('img', src=True):
        src = img['src']
        if src.startswith('http'):
            images.append(src)
    return images[:10]


def parse_date(date_str: str) -> Optional[str]:
    """Try to parse various date formats into ISO format."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d',
        '%B %d, %Y', '%b %d, %Y', '%d %B %Y', '%d %b %Y',
        '%B %d, %Y at %I:%M%p', '%m/%d/%Y', '%d/%m/%Y'
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str[:25], fmt)
            return dt.isoformat()
        except ValueError:
            continue

    return None


def check_source_accessibility(url: str, timeout: int = 10) -> Dict:
    """Check if a source URL is accessible. Used by dashboard."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={'User-Agent': 'Mozilla/5.0'})
        return {
            'accessible': resp.status_code < 400,
            'status_code': resp.status_code,
            'final_url': resp.url
        }
    except Exception as e:
        return {
            'accessible': False,
            'status_code': None,
            'error': str(e)
        }
