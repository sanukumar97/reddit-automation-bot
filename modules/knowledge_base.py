"""
Knowledge Base Module
Handles all database operations - storing and retrieving articles,
posts, sources, and logs from the SQLite database.
"""

import sqlite3
import os
import json
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Path to the database file
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'knowledge_base.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'schema.sql')


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    return conn


def initialize_database():
    """Create the database and all tables from schema.sql."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(SCHEMA_PATH, 'r') as f:
        schema = f.read()
    conn = get_connection()
    try:
        conn.executescript(schema)
        conn.commit()
        logger.info("Database initialized successfully")
    finally:
        conn.close()


# ==============================================================
# SOURCE MANAGEMENT
# ==============================================================

def sync_sources_from_config(sources_config: List[Dict]) -> None:
    """Sync sources from YAML config into the database."""
    conn = get_connection()
    try:
        for src in sources_config:
            conn.execute("""
                INSERT INTO sources (id, name, url, type, category, priority, enabled, tags)
                VALUES (:id, :name, :url, :type, :category, :priority, :enabled, :tags)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    url=excluded.url,
                    type=excluded.type,
                    category=excluded.category,
                    priority=excluded.priority,
                    enabled=excluded.enabled,
                    tags=excluded.tags,
                    updated_at=CURRENT_TIMESTAMP
            """, {
                'id': src.get('id'),
                'name': src.get('name'),
                'url': src.get('url'),
                'type': src.get('type', 'webpage'),
                'category': src.get('category', ''),
                'priority': src.get('priority', 3),
                'enabled': 1 if src.get('enabled', True) else 0,
                'tags': json.dumps(src.get('tags', []))
            })
        conn.commit()
        logger.info(f"Synced {len(sources_config)} sources from config")
    finally:
        conn.close()


def get_all_sources(enabled_only: bool = True) -> List[Dict]:
    """Get all sources from the database."""
    conn = get_connection()
    try:
        query = "SELECT * FROM sources"
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY priority ASC, name ASC"
        rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_source_last_checked(source_id: str, found_new: bool = False):
    """Update when a source was last checked."""
    conn = get_connection()
    try:
        now = datetime.utcnow().isoformat()
        if found_new:
            conn.execute("""
                UPDATE sources SET last_checked=?, last_new_content=? WHERE id=?
            """, (now, now, source_id))
        else:
            conn.execute("UPDATE sources SET last_checked=? WHERE id=?", (now, source_id))
        conn.commit()
    finally:
        conn.close()


def toggle_source(source_id: str, enabled: bool):
    """Enable or disable a source."""
    conn = get_connection()
    try:
        conn.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))
        conn.commit()
    finally:
        conn.close()


# ==============================================================
# ARTICLE / KNOWLEDGE BASE OPERATIONS
# ==============================================================

def url_already_processed(url: str) -> bool:
    """Check if a URL has already been processed (duplicate check)."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM processed_urls WHERE url_hash=?", (url_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_url_processed(url: str, content_hash: str = None, processing_type: str = 'article'):
    """Mark a URL as processed to prevent duplicates."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO processed_urls (url, url_hash, content_hash, processing_type)
            VALUES (?, ?, ?, ?)
        """, (url, url_hash, content_hash, processing_type))
        conn.commit()
    finally:
        conn.close()


def save_article(article: Dict) -> Optional[int]:
    """Save a scraped article to the knowledge base. Returns article ID."""
    conn = get_connection()
    try:
        # Check if article URL already exists
        existing = conn.execute(
            "SELECT id FROM articles WHERE url=?", (article['url'],)
        ).fetchone()
        if existing:
            logger.debug(f"Article already exists: {article['url']}")
            return existing['id']

        cursor = conn.execute("""
            INSERT INTO articles (
                source_id, title, url, content, images, tags, author,
                published_at, category, source_website, word_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article.get('source_id'),
            article.get('title', 'Untitled'),
            article.get('url'),
            article.get('content', ''),
            json.dumps(article.get('images', [])),
            json.dumps(article.get('tags', [])),
            article.get('author', ''),
            article.get('published_at'),
            article.get('category', ''),
            article.get('source_website', ''),
            article.get('word_count', 0)
        ))
        conn.commit()

        article_id = cursor.lastrowid
        # Mark URL as processed
        mark_url_processed(article['url'], processing_type='article')

        # Update source article count
        conn.execute(
            "UPDATE sources SET total_articles=total_articles+1 WHERE id=?",
            (article.get('source_id'),)
        )
        conn.commit()

        logger.info(f"Saved article: {article.get('title', 'Unknown')[:60]}")
        return article_id
    except Exception as e:
        logger.error(f"Error saving article: {e}")
        return None
    finally:
        conn.close()


def save_article_analysis(article_id: int, analysis: Dict):
    """Save AI analysis results for an article."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE articles SET
                summary=?,
                key_insights=?,
                major_topics=?,
                trends=?,
                discussion_opportunities=?,
                relevance_score=?,
                analyzed_at=?,
                status='analyzed'
            WHERE id=?
        """, (
            analysis.get('summary', ''),
            json.dumps(analysis.get('key_insights', [])),
            json.dumps(analysis.get('major_topics', [])),
            json.dumps(analysis.get('trends', [])),
            json.dumps(analysis.get('discussion_opportunities', [])),
            analysis.get('relevance_score', 0.5),
            datetime.utcnow().isoformat(),
            article_id
        ))
        conn.commit()
    finally:
        conn.close()


def get_articles_for_processing(limit: int = 10) -> List[Dict]:
    """Get articles that have been analyzed but not yet turned into posts."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, s.name as source_name
            FROM articles a
            LEFT JOIN sources s ON a.source_id = s.id
            WHERE a.status = 'analyzed'
            ORDER BY a.relevance_score DESC, a.discovered_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_new_articles(limit: int = 20) -> List[Dict]:
    """Get newly scraped articles that need AI analysis."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, s.name as source_name, s.category as source_category
            FROM articles a
            LEFT JOIN sources s ON a.source_id = s.id
            WHERE a.status = 'new'
            AND length(a.content) > 100
            ORDER BY a.discovered_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_article_by_id(article_id: int) -> Optional[Dict]:
    """Get a single article by ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_article_status(article_id: int, status: str, error: str = None):
    """Update article processing status."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE articles SET status=?, error_message=? WHERE id=?",
            (status, error, article_id)
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_old_content(days_old: int = 2) -> int:
    """
    Wipe scraped content from articles that were POSTED more than N days ago.
    Default: 2 days after posting.

    What gets deleted (heavy data):
      - Full article content text
      - AI-generated summary
      - Key insights, topics, trends, discussion opportunities
      - Images list

    What is KEPT forever (needed for duplicate prevention):
      - Title, URL, published_at, discovered_at
      - Source info, category, tags, word_count
      - Status and error_message

    Returns number of articles cleaned up.
    """
    conn = get_connection()
    try:
        # Clean articles that were posted and whose reddit post is older than N days
        result = conn.execute("""
            UPDATE articles SET
                content = NULL,
                summary = NULL,
                key_insights = NULL,
                major_topics = NULL,
                trends = NULL,
                discussion_opportunities = NULL,
                images = NULL
            WHERE id IN (
                SELECT DISTINCT gp.article_id
                FROM generated_posts gp
                JOIN reddit_posts rp ON gp.id = rp.generated_post_id
                WHERE rp.status = 'posted'
                AND rp.posted_at < datetime('now', ? || ' days')
            )
            AND content IS NOT NULL
        """, (f'-{days_old}',))
        posted_count = result.rowcount

        # Also clean skipped/error articles older than N days (no post was ever made)
        result2 = conn.execute("""
            UPDATE articles SET
                content = NULL,
                summary = NULL,
                key_insights = NULL,
                major_topics = NULL,
                trends = NULL,
                discussion_opportunities = NULL,
                images = NULL
            WHERE status IN ('skipped', 'error')
            AND content IS NOT NULL
            AND discovered_at < datetime('now', ? || ' days')
        """, (f'-{days_old}',))
        skipped_count = result2.rowcount

        conn.commit()
        total = posted_count + skipped_count
        if total > 0:
            logger.info(
                f"Storage cleanup: cleared content from {total} article(s) "
                f"({posted_count} posted, {skipped_count} skipped/error) "
                f"older than {days_old} day(s)"
            )
        return total
    finally:
        conn.close()


def get_storage_stats() -> Dict:
    """Get database storage statistics."""
    conn = get_connection()
    try:
        stats = {}
        stats['total_articles'] = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        stats['articles_with_content'] = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE content IS NOT NULL"
        ).fetchone()[0]
        stats['articles_cleaned'] = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE content IS NULL AND status IN ('posted','skipped','error')"
        ).fetchone()[0]

        # Estimate DB file size
        db_path = DB_PATH
        if os.path.exists(db_path):
            size_bytes = os.path.getsize(db_path)
            stats['db_size_mb'] = round(size_bytes / (1024 * 1024), 2)
        else:
            stats['db_size_mb'] = 0

        return stats
    finally:
        conn.close()


def get_knowledge_base_stats() -> Dict:
    """Get summary statistics for the knowledge base."""
    conn = get_connection()
    try:
        stats = {}
        stats['total_articles'] = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        stats['analyzed_articles'] = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status='analyzed'"
        ).fetchone()[0]
        stats['total_sources'] = conn.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
        stats['total_posts'] = conn.execute("SELECT COUNT(*) FROM reddit_posts").fetchone()[0]
        stats['pending_drafts'] = conn.execute(
            "SELECT COUNT(*) FROM generated_posts WHERE status='draft'"
        ).fetchone()[0]
        stats['recent_articles'] = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE discovered_at > datetime('now', '-24 hours')"
        ).fetchone()[0]
        return stats
    finally:
        conn.close()


# ==============================================================
# GENERATED POSTS
# ==============================================================

def save_generated_post(post: Dict) -> Optional[int]:
    """Save an AI-generated Reddit post draft."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO generated_posts (
                article_id, subreddit, title, body, post_type, flair,
                status, approval_mode, generation_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post.get('article_id'),
            post.get('subreddit'),
            post.get('title'),
            post.get('body'),
            post.get('post_type', 'text'),
            post.get('flair', ''),
            post.get('status', 'draft'),
            post.get('approval_mode', 'B'),
            post.get('generation_model', 'unknown')
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_pending_posts(limit: int = 20) -> List[Dict]:
    """Get posts waiting for review/approval."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT gp.*, a.title as article_title, a.url as article_url,
                   a.summary as article_summary
            FROM generated_posts gp
            LEFT JOIN articles a ON gp.article_id = a.id
            WHERE gp.status = 'draft'
            ORDER BY gp.generated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_approved_posts() -> List[Dict]:
    """Get posts approved for publishing."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT gp.*, a.title as article_title, a.url as article_url
            FROM generated_posts gp
            LEFT JOIN articles a ON gp.article_id = a.id
            WHERE gp.status = 'approved'
            ORDER BY gp.reviewed_at ASC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_post_status(post_id: int, status: str, notes: str = None):
    """Update the status of a generated post (approve/reject/etc)."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE generated_posts
            SET status=?, reviewer_notes=?, reviewed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (status, notes, post_id))
        conn.commit()
    finally:
        conn.close()


def get_post_by_id(post_id: int) -> Optional[Dict]:
    """Get a generated post by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM generated_posts WHERE id=?", (post_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==============================================================
# REDDIT POSTS (published)
# ==============================================================

def save_reddit_post(post_data: Dict) -> Optional[int]:
    """Record a successfully published Reddit post."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO reddit_posts (
                generated_post_id, article_id, reddit_post_id, reddit_url,
                subreddit, reddit_account, title, body, post_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_data.get('generated_post_id'),
            post_data.get('article_id'),
            post_data.get('reddit_post_id'),
            post_data.get('reddit_url'),
            post_data.get('subreddit'),
            post_data.get('reddit_account'),
            post_data.get('title'),
            post_data.get('body'),
            post_data.get('post_type', 'text')
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_post_metrics(reddit_post_id: str, metrics: Dict):
    """Update performance metrics for a Reddit post."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE reddit_posts SET
                upvotes=?, score=?, upvote_ratio=?, num_comments=?,
                engagement_rate=?, last_tracked=CURRENT_TIMESTAMP
            WHERE reddit_post_id=?
        """, (
            metrics.get('upvotes', 0),
            metrics.get('score', 0),
            metrics.get('upvote_ratio', 0.0),
            metrics.get('num_comments', 0),
            metrics.get('engagement_rate', 0.0),
            reddit_post_id
        ))
        conn.commit()
    finally:
        conn.close()


def get_published_posts(limit: int = 50) -> List[Dict]:
    """Get all published Reddit posts with performance data."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT rp.*, a.title as article_title
            FROM reddit_posts rp
            LEFT JOIN generated_posts gp ON rp.generated_post_id = gp.id
            LEFT JOIN articles a ON rp.article_id = a.id
            ORDER BY rp.posted_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def posts_to_subreddit_today(subreddit: str) -> int:
    """Count posts to a subreddit in the last 24 hours."""
    conn = get_connection()
    try:
        count = conn.execute("""
            SELECT COUNT(*) FROM reddit_posts
            WHERE subreddit=? AND posted_at > datetime('now', '-24 hours')
            AND status='posted'
        """, (subreddit,)).fetchone()[0]
        return count
    finally:
        conn.close()


def recently_posted_to_subreddit(subreddit: str, hours: int = 2) -> bool:
    """Check if we posted to this subreddit recently (rate limiting)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM reddit_posts
            WHERE subreddit=? AND posted_at > datetime('now', ? || ' hours')
            AND status='posted'
        """, (subreddit, f'-{hours}')).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def article_already_posted(article_id: int, subreddit: str) -> bool:
    """Check if an article was already posted to a specific subreddit."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM reddit_posts rp
            JOIN generated_posts gp ON rp.generated_post_id = gp.id
            WHERE gp.article_id=? AND rp.subreddit=?
        """, (article_id, subreddit)).fetchone()
        return row[0] > 0
    finally:
        conn.close()


# ==============================================================
# LOGGING
# ==============================================================

def log_to_db(level: str, module: str, message: str, details: Dict = None):
    """Log a message to the database."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO system_logs (level, module, message, details)
            VALUES (?, ?, ?, ?)
        """, (level, module, message, json.dumps(details) if details else None))
        conn.commit()
    except Exception:
        pass  # Don't let logging errors crash the system
    finally:
        conn.close()


def get_recent_logs(limit: int = 100, level: str = None) -> List[Dict]:
    """Get recent system logs."""
    conn = get_connection()
    try:
        if level:
            rows = conn.execute("""
                SELECT * FROM system_logs WHERE level=?
                ORDER BY created_at DESC LIMIT ?
            """, (level, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM system_logs
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ==============================================================
# ANALYTICS
# ==============================================================

def get_analytics_summary() -> Dict:
    """Get comprehensive analytics data."""
    conn = get_connection()
    try:
        data = {}

        # Overall stats
        data['total_articles'] = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        data['total_posts_generated'] = conn.execute("SELECT COUNT(*) FROM generated_posts").fetchone()[0]
        data['total_posts_published'] = conn.execute(
            "SELECT COUNT(*) FROM reddit_posts WHERE status='posted'"
        ).fetchone()[0]
        data['pending_review'] = conn.execute(
            "SELECT COUNT(*) FROM generated_posts WHERE status='draft'"
        ).fetchone()[0]

        # Performance averages
        perf = conn.execute("""
            SELECT AVG(score) as avg_score, AVG(num_comments) as avg_comments,
                   SUM(upvotes) as total_upvotes, MAX(score) as best_score
            FROM reddit_posts WHERE status='posted'
        """).fetchone()
        data['avg_score'] = round(perf['avg_score'] or 0, 1)
        data['avg_comments'] = round(perf['avg_comments'] or 0, 1)
        data['total_upvotes'] = perf['total_upvotes'] or 0
        data['best_score'] = perf['best_score'] or 0

        # Per-subreddit breakdown
        subreddit_stats = conn.execute("""
            SELECT subreddit,
                   COUNT(*) as post_count,
                   AVG(score) as avg_score,
                   SUM(num_comments) as total_comments
            FROM reddit_posts WHERE status='posted'
            GROUP BY subreddit
            ORDER BY avg_score DESC
        """).fetchall()
        data['subreddit_stats'] = [dict(row) for row in subreddit_stats]

        # Posts over time (last 30 days)
        daily_posts = conn.execute("""
            SELECT DATE(posted_at) as date, COUNT(*) as count
            FROM reddit_posts WHERE status='posted'
            AND posted_at > datetime('now', '-30 days')
            GROUP BY DATE(posted_at)
            ORDER BY date ASC
        """).fetchall()
        data['daily_posts'] = [dict(row) for row in daily_posts]

        # Top performing posts
        top_posts = conn.execute("""
            SELECT title, subreddit, score, num_comments, reddit_url, posted_at
            FROM reddit_posts WHERE status='posted'
            ORDER BY score DESC LIMIT 5
        """).fetchall()
        data['top_posts'] = [dict(row) for row in top_posts]

        # Articles by category
        category_dist = conn.execute("""
            SELECT category, COUNT(*) as count
            FROM articles
            GROUP BY category
            ORDER BY count DESC
        """).fetchall()
        data['category_distribution'] = [dict(row) for row in category_dist]

        return data
    finally:
        conn.close()
