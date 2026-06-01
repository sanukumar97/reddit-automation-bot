"""
Performance Tracker Module
Fetches updated metrics from Reddit for published posts
and stores them in the database for analytics.
"""

import logging
import time
from typing import Dict, List

from . import knowledge_base as kb

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Tracks performance of published Reddit posts."""

    def __init__(self, reddit_client=None):
        self.reddit_client = reddit_client

    def run_tracking_cycle(self) -> Dict:
        """
        Fetch updated metrics for all recently published posts.
        Returns summary of posts updated.
        """
        if not self.reddit_client:
            logger.warning("No Reddit client configured for performance tracking")
            return {'updated': 0, 'failed': 0}

        posts = kb.get_published_posts(limit=100)

        summary = {'updated': 0, 'failed': 0, 'skipped': 0}

        for post in posts:
            reddit_id = post.get('reddit_post_id')
            if not reddit_id:
                summary['skipped'] += 1
                continue

            try:
                metrics = self.reddit_client.get_post_metrics(reddit_id)
                if metrics:
                    kb.update_post_metrics(reddit_id, metrics)
                    summary['updated'] += 1
                else:
                    summary['failed'] += 1

                time.sleep(1)  # Be polite to Reddit API

            except Exception as e:
                logger.debug(f"Could not track post {reddit_id}: {e}")
                summary['failed'] += 1

        logger.info(
            f"Performance tracking: {summary['updated']} updated, "
            f"{summary['failed']} failed, {summary['skipped']} skipped"
        )
        return summary

    def get_performance_report(self) -> Dict:
        """Generate a performance summary report."""
        return kb.get_analytics_summary()

    def get_best_performing_posts(self, limit: int = 10) -> List[Dict]:
        """Get top performing posts for learning what works."""
        posts = kb.get_published_posts(limit=200)
        sorted_posts = sorted(posts, key=lambda x: x.get('score', 0), reverse=True)
        return sorted_posts[:limit]

    def get_subreddit_insights(self) -> List[Dict]:
        """Get per-subreddit performance insights."""
        analytics = kb.get_analytics_summary()
        return analytics.get('subreddit_stats', [])
