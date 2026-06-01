"""
Reddit Client Module
Handles all interaction with the Reddit API using PRAW.
Supports posting, rate limiting, duplicate prevention,
and multiple account management.
"""

import praw
import logging
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from . import knowledge_base as kb

logger = logging.getLogger(__name__)


class RedditClient:
    """Manages Reddit API interactions for posting content."""

    def __init__(self, config: Dict):
        self.config = config
        self.reddit_config = config.get('reddit', {})
        self.posting_config = config.get('posting', {})
        self._reddit = None

    def _get_reddit(self) -> Optional[praw.Reddit]:
        """Get or create a Reddit API connection."""
        if self._reddit is not None:
            return self._reddit

        try:
            self._reddit = praw.Reddit(
                client_id=self.reddit_config.get('client_id'),
                client_secret=self.reddit_config.get('client_secret'),
                username=self.reddit_config.get('username'),
                password=self.reddit_config.get('password'),
                user_agent=self.reddit_config.get(
                    'user_agent',
                    f"RedditBot/1.0 by {self.reddit_config.get('username', 'user')}"
                )
            )
            # Verify credentials work
            username = self._reddit.user.me()
            logger.info(f"Reddit authenticated as: u/{username}")
            return self._reddit
        except Exception as e:
            logger.error(f"Reddit authentication failed: {e}")
            logger.error("Check your credentials in config/settings.yaml")
            self._reddit = None
            return None

    def test_connection(self) -> Dict:
        """Test Reddit API connection and return status."""
        try:
            reddit = self._get_reddit()
            if reddit:
                me = reddit.user.me()
                karma = me.link_karma + me.comment_karma
                return {
                    'connected': True,
                    'username': str(me),
                    'karma': karma,
                    'link_karma': me.link_karma,
                    'comment_karma': me.comment_karma,
                    'account_age_days': (
                        datetime.utcnow() -
                        datetime.utcfromtimestamp(me.created_utc)
                    ).days
                }
        except Exception as e:
            return {'connected': False, 'error': str(e)}
        return {'connected': False, 'error': 'Unknown error'}

    def post_to_reddit(self, post_data: Dict) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Submit a post to Reddit.
        Returns: (success, reddit_post_id, reddit_url)
        """
        reddit = self._get_reddit()
        if not reddit:
            return False, None, "Reddit not connected"

        subreddit_name = post_data.get('subreddit', '')
        title = post_data.get('title', '')
        body = post_data.get('body', '')
        post_type = post_data.get('post_type', 'text')
        flair = post_data.get('flair', '')
        article_url = post_data.get('article_url', '')

        if not subreddit_name or not title:
            return False, None, "Missing subreddit or title"

        # Check daily post limit
        max_per_day = self.posting_config.get('max_posts_per_day', 10)
        today_count = kb.posts_to_subreddit_today(subreddit_name)
        if today_count >= max_per_day:
            msg = f"Daily post limit ({max_per_day}) reached for r/{subreddit_name}"
            logger.warning(msg)
            return False, None, msg

        # Check minimum gap between posts to same subreddit
        min_hours = self.posting_config.get('min_hours_between_posts', 2)
        if kb.recently_posted_to_subreddit(subreddit_name, hours=min_hours):
            msg = f"Too soon to post again to r/{subreddit_name} (min {min_hours}h gap)"
            logger.warning(msg)
            return False, None, msg

        try:
            subreddit = reddit.subreddit(subreddit_name)

            # Verify subreddit is accessible
            try:
                _ = subreddit.id
            except Exception as e:
                return False, None, f"Cannot access r/{subreddit_name}: {e}"

            submission = None

            if post_type == 'link' and article_url:
                # Link post
                submission = subreddit.submit(
                    title=title,
                    url=article_url,
                    flair_id=self._get_flair_id(subreddit, flair) if flair else None
                )
            elif post_type == 'hybrid' and article_url:
                # Text post with link in body
                body_with_link = f"{body}\n\n[Source]({article_url})" if body else f"[Source]({article_url})"
                submission = subreddit.submit(
                    title=title,
                    selftext=body_with_link,
                    flair_id=self._get_flair_id(subreddit, flair) if flair else None
                )
            else:
                # Text post (default)
                submission = subreddit.submit(
                    title=title,
                    selftext=body,
                    flair_id=self._get_flair_id(subreddit, flair) if flair else None
                )

            if submission:
                post_url = f"https://reddit.com{submission.permalink}"
                logger.info(f"Successfully posted to r/{subreddit_name}: {post_url}")

                # Wait between posts to avoid rate limiting
                delay = self.posting_config.get('post_delay_seconds', 30)
                if delay > 0:
                    logger.debug(f"Waiting {delay}s before next post...")
                    time.sleep(delay)

                return True, submission.id, post_url

        except praw.exceptions.RedditAPIException as e:
            for item in e.items:
                if item.error_type == 'SUBREDDIT_NOTALLOWED':
                    msg = f"Not allowed to post to r/{subreddit_name} (banned or restricted)"
                elif item.error_type == 'RATELIMIT':
                    msg = f"Reddit rate limit hit. Wait and try again."
                elif item.error_type == 'THREAD_LOCKED':
                    msg = "Subreddit is locked"
                else:
                    msg = f"Reddit API error: {item.error_type} - {item.message}"
                logger.error(msg)
                return False, None, msg

        except Exception as e:
            msg = f"Unexpected error posting to Reddit: {e}"
            logger.error(msg)
            return False, None, msg

        return False, None, "Unknown failure"

    def _get_flair_id(self, subreddit, flair_text: str) -> Optional[str]:
        """Find flair ID by text. Returns None if not found."""
        try:
            for flair in subreddit.flair.link_templates:
                if flair_text.lower() in flair.get('text', '').lower():
                    return flair.get('id')
        except Exception:
            pass
        return None

    def get_post_metrics(self, reddit_post_id: str) -> Optional[Dict]:
        """Fetch current metrics for a published post."""
        reddit = self._get_reddit()
        if not reddit:
            return None

        try:
            submission = reddit.submission(id=reddit_post_id)
            submission._fetch()

            upvotes = submission.ups
            downvotes = submission.downs
            score = submission.score
            num_comments = submission.num_comments
            upvote_ratio = submission.upvote_ratio

            # Simple engagement rate: (score + comments) / max(1, views)
            engagement_rate = round((score + num_comments) / 100.0, 4)

            return {
                'upvotes': upvotes,
                'downvotes': downvotes,
                'score': score,
                'num_comments': num_comments,
                'upvote_ratio': upvote_ratio,
                'engagement_rate': engagement_rate
            }
        except Exception as e:
            logger.debug(f"Could not fetch metrics for post {reddit_post_id}: {e}")
            return None

    def get_subreddit_info(self, subreddit_name: str) -> Optional[Dict]:
        """Get information about a subreddit."""
        reddit = self._get_reddit()
        if not reddit:
            return None

        try:
            sub = reddit.subreddit(subreddit_name)
            return {
                'name': sub.display_name,
                'title': sub.title,
                'description': sub.public_description[:500] if sub.public_description else '',
                'subscribers': sub.subscribers,
                'over18': sub.over18,
                'submission_type': sub.submission_type,
                'rules': [r.short_name for r in sub.rules][:10]
            }
        except Exception as e:
            logger.error(f"Could not get subreddit info for r/{subreddit_name}: {e}")
            return None

    def process_approved_posts(self, approved_posts: List[Dict]) -> Dict:
        """
        Process and publish all approved posts.
        Returns summary of results.
        """
        results = {'published': 0, 'failed': 0, 'skipped': 0}

        for post in approved_posts:
            post_id = post.get('id')
            article_id = post.get('article_id')
            subreddit = post.get('subreddit')

            # Final duplicate check
            if article_id and subreddit and kb.article_already_posted(article_id, subreddit):
                logger.info(f"Skipping: article already posted to r/{subreddit}")
                kb.update_post_status(post_id, 'skipped', 'Already posted to this subreddit')
                results['skipped'] += 1
                continue

            # Fetch article URL for link posts
            article = None
            if article_id:
                article = kb.get_article_by_id(article_id)

            post_payload = {
                'subreddit': subreddit,
                'title': post.get('title'),
                'body': post.get('body'),
                'post_type': post.get('post_type', 'text'),
                'flair': post.get('flair', ''),
                'article_url': article.get('url', '') if article else ''
            }

            success, reddit_id, url_or_error = self.post_to_reddit(post_payload)

            if success:
                # Record the published post
                kb.save_reddit_post({
                    'generated_post_id': post_id,
                    'article_id': article_id,
                    'reddit_post_id': reddit_id,
                    'reddit_url': url_or_error,
                    'subreddit': subreddit,
                    'reddit_account': self.reddit_config.get('username'),
                    'title': post.get('title'),
                    'body': post.get('body'),
                    'post_type': post.get('post_type', 'text')
                })
                kb.update_post_status(post_id, 'posted')
                kb.mark_article_status(article_id, 'posted')
                results['published'] += 1
                logger.info(f"Published to r/{subreddit}: {url_or_error}")
            else:
                kb.update_post_status(post_id, 'failed', url_or_error)
                results['failed'] += 1
                logger.error(f"Failed to publish to r/{subreddit}: {url_or_error}")

        return results
