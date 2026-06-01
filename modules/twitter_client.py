"""
Twitter/X Client Module
Handles tweet generation and posting via the Twitter API v2 (Tweepy).
Supports single tweets and multi-tweet threads.

Free tier: 1,500 tweets/month — more than enough for content automation.
Get credentials at: https://developer.twitter.com/en/portal/dashboard
"""

import json
import logging
import sqlite3
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# DB path (same as knowledge_base)
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'knowledge_base.db')

# Path to Twitter writing instructions
TWITTER_INSTRUCTIONS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'config', 'twitter_writing_instructions.md'
)


# ---------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_twitter_draft(draft: Dict) -> Optional[int]:
    """Save a generated tweet draft to the database."""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO twitter_posts (
                article_id, tweet_text, tweet_type, thread_tweets,
                status, approval_mode
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            draft.get('article_id'),
            draft.get('tweet_text', ''),
            draft.get('tweet_type', 'single'),
            json.dumps(draft.get('thread_tweets', [])),
            draft.get('status', 'draft'),
            draft.get('approval_mode', 'B')
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pending_tweets(limit: int = 20) -> List[Dict]:
    """Get tweets awaiting approval."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT tp.*, a.title as article_title, a.url as article_url
            FROM twitter_posts tp
            LEFT JOIN articles a ON tp.article_id = a.id
            WHERE tp.status = 'draft'
            ORDER BY tp.generated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_approved_tweets() -> List[Dict]:
    """Get tweets approved for posting."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT tp.*, a.title as article_title
            FROM twitter_posts tp
            LEFT JOIN articles a ON tp.article_id = a.id
            WHERE tp.status = 'approved'
            ORDER BY tp.generated_at ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_published_tweets(limit: int = 50) -> List[Dict]:
    """Get all published tweets."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT tp.*, a.title as article_title
            FROM twitter_posts tp
            LEFT JOIN articles a ON tp.article_id = a.id
            WHERE tp.status = 'posted'
            ORDER BY tp.posted_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_tweet_status(tweet_id: int, status: str, notes: str = None):
    """Update a tweet's status."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE twitter_posts SET status=? WHERE id=?",
            (status, tweet_id)
        )
        conn.commit()
    finally:
        conn.close()


def record_posted_tweet(draft_id: int, twitter_id: str, url: str, account: str):
    """Mark a tweet as successfully posted."""
    conn = _get_conn()
    try:
        conn.execute("""
            UPDATE twitter_posts
            SET status='posted', tweet_id=?, tweet_url=?, twitter_account=?, posted_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (twitter_id, url, account, draft_id))
        conn.commit()
    finally:
        conn.close()


def tweets_posted_today() -> int:
    """Count tweets posted in the last 24 hours (rate limit check)."""
    conn = _get_conn()
    try:
        count = conn.execute("""
            SELECT COUNT(*) FROM twitter_posts
            WHERE status='posted' AND posted_at > datetime('now', '-24 hours')
        """).fetchone()[0]
        return count
    finally:
        conn.close()


def article_already_tweeted(article_id: int) -> bool:
    """Check if an article was already tweeted."""
    conn = _get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM twitter_posts WHERE article_id=? AND status='posted'",
            (article_id,)
        ).fetchone()[0]
        return count > 0
    finally:
        conn.close()


# ---------------------------------------------------------------
# TWEET GENERATOR
# ---------------------------------------------------------------

def load_twitter_instructions() -> str:
    """Load Twitter writing instructions."""
    try:
        with open(TWITTER_INSTRUCTIONS_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return DEFAULT_TWITTER_INSTRUCTIONS


class TweetGenerator:
    """Generates tweets and threads from article analysis using AI."""

    def __init__(self, config: Dict, analyzer=None):
        self.config = config
        self.analyzer = analyzer
        self.twitter_config = config.get('twitter', {})

    def generate_tweet(self, article: Dict, analysis: Dict) -> Optional[Dict]:
        """Generate a tweet or thread from an article."""
        if not self.analyzer:
            logger.error("No analyzer configured for tweet generation")
            return None

        tweet_type = self.twitter_config.get('default_tweet_type', 'single')
        instructions = load_twitter_instructions()
        summary = analysis.get('summary', '')
        key_insights = analysis.get('key_insights', [])
        best_angle = analysis.get('best_post_angle', '')

        if tweet_type == 'thread':
            return self._generate_thread(article, analysis, instructions)
        else:
            return self._generate_single_tweet(article, analysis, instructions)

    def _generate_single_tweet(self, article, analysis, instructions) -> Optional[Dict]:
        """Generate a single tweet (max 280 chars)."""
        summary = analysis.get('summary', '')
        key_insights = analysis.get('key_insights', [])[:3]
        hashtags = self.twitter_config.get('default_hashtags', [])
        hashtag_str = ' '.join(f'#{h}' for h in hashtags[:3]) if hashtags else ''

        prompt = f"""You are writing a tweet for Twitter/X.

WRITING INSTRUCTIONS:
{instructions}

ARTICLE CONTEXT:
Title: {article.get('title', '')}
Summary: {summary}
Key Insights: {chr(10).join(f'- {i}' for i in key_insights)}

TASK: Write ONE tweet (max 280 characters total including hashtags).

Rules:
- Conversational, human, not corporate
- Thought-provoking or insight-driven
- May include 1-2 relevant hashtags if they add value
- No clickbait, no "check this out"
- Hashtags to consider (optional): {hashtag_str}

Respond ONLY with valid JSON:
{{"tweet": "your tweet text here (max 280 chars total)"}}"""

        response = self.analyzer._call_llm(prompt)
        if not response:
            return None

        data = self._parse_json(response)
        if not data or 'tweet' not in data:
            return None

        tweet_text = data['tweet'].strip()
        # Enforce 280 char limit
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + '...'

        return {
            'article_id': article.get('id'),
            'tweet_text': tweet_text,
            'tweet_type': 'single',
            'thread_tweets': []
        }

    def _generate_thread(self, article, analysis, instructions) -> Optional[Dict]:
        """Generate a multi-tweet thread (3-5 tweets)."""
        summary = analysis.get('summary', '')
        key_insights = analysis.get('key_insights', [])
        discussion_ops = analysis.get('discussion_opportunities', [])
        hashtags = self.twitter_config.get('default_hashtags', [])
        hashtag_str = ' '.join(f'#{h}' for h in hashtags[:3]) if hashtags else ''

        prompt = f"""You are writing a Twitter thread.

WRITING INSTRUCTIONS:
{instructions}

ARTICLE CONTEXT:
Title: {article.get('title', '')}
Summary: {summary}
Key Insights: {chr(10).join(f'- {i}' for i in key_insights[:5])}
Discussion angles: {chr(10).join(f'- {d}' for d in discussion_ops[:2])}

TASK: Write a Twitter thread of 3-5 tweets.

Thread structure:
- Tweet 1: Hook — surprising fact, bold observation, or question (max 240 chars)
- Tweet 2-3: Key insights or context (max 270 chars each)
- Tweet 4: Your take or implication (max 270 chars)
- Tweet 5 (optional): Question or CTA to engage followers (max 270 chars)

Rules:
- Each tweet stands alone but flows naturally as a thread
- Conversational, human voice
- Number tweets like "1/" "2/" etc. at the START
- Add hashtags only to the LAST tweet: {hashtag_str}

Respond ONLY with valid JSON:
{{"tweets": ["1/ First tweet here", "2/ Second tweet here", "3/ Third tweet here"]}}"""

        response = self.analyzer._call_llm(prompt)
        if not response:
            return None

        data = self._parse_json(response)
        if not data or 'tweets' not in data:
            return None

        tweets = [t.strip() for t in data['tweets'] if t.strip()]
        if not tweets:
            return None

        # Use first tweet as main tweet text
        main_tweet = tweets[0][:280]

        return {
            'article_id': article.get('id'),
            'tweet_text': main_tweet,
            'tweet_type': 'thread',
            'thread_tweets': [t[:280] for t in tweets]
        }

    def _parse_json(self, text: str) -> Optional[Dict]:
        """Parse JSON from LLM response."""
        text = text.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end+1]
        try:
            return json.loads(text)
        except Exception:
            return None


# ---------------------------------------------------------------
# TWITTER API CLIENT
# ---------------------------------------------------------------

class TwitterClient:
    """Posts tweets to Twitter/X via API v2 (Tweepy)."""

    def __init__(self, config: Dict):
        self.config = config
        self.twitter_config = config.get('twitter', {})
        self._client = None

    def _get_client(self):
        """Get or create Tweepy client."""
        if self._client:
            return self._client
        try:
            import tweepy
            self._client = tweepy.Client(
                consumer_key=self.twitter_config.get('api_key'),
                consumer_secret=self.twitter_config.get('api_secret'),
                access_token=self.twitter_config.get('access_token'),
                access_token_secret=self.twitter_config.get('access_token_secret'),
                bearer_token=self.twitter_config.get('bearer_token'),
                wait_on_rate_limit=True
            )
            return self._client
        except ImportError:
            logger.error("tweepy not installed. Run: pip install tweepy")
            return None
        except Exception as e:
            logger.error(f"Twitter auth error: {e}")
            return None

    def test_connection(self) -> Dict:
        """Test Twitter API connection."""
        try:
            import tweepy
            client = self._get_client()
            if not client:
                return {'connected': False, 'error': 'Could not create client'}
            me = client.get_me()
            if me.data:
                return {
                    'connected': True,
                    'username': me.data.username,
                    'name': me.data.name,
                    'id': str(me.data.id)
                }
        except ImportError:
            return {'connected': False, 'error': 'tweepy not installed — run: pip install tweepy'}
        except Exception as e:
            return {'connected': False, 'error': str(e)}
        return {'connected': False, 'error': 'Unknown error'}

    def post_tweet(self, tweet_data: Dict) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Post a tweet or thread.
        Returns (success, tweet_id, tweet_url or error_msg)
        """
        client = self._get_client()
        if not client:
            return False, None, "Twitter not connected"

        # Daily limit check
        max_per_day = self.twitter_config.get('max_tweets_per_day', 20)
        if tweets_posted_today() >= max_per_day:
            return False, None, f"Daily tweet limit ({max_per_day}) reached"

        tweet_type = tweet_data.get('tweet_type', 'single')
        username = self.twitter_config.get('username', '')

        try:
            if tweet_type == 'thread':
                thread = tweet_data.get('thread_tweets', [tweet_data.get('tweet_text', '')])
                return self._post_thread(client, thread, username)
            else:
                text = tweet_data.get('tweet_text', '')
                return self._post_single(client, text, username)

        except Exception as e:
            logger.error(f"Tweet posting error: {e}")
            return False, None, str(e)

    def _post_single(self, client, text: str, username: str) -> Tuple[bool, str, str]:
        """Post a single tweet."""
        response = client.create_tweet(text=text)
        tweet_id = str(response.data['id'])
        url = f"https://twitter.com/{username}/status/{tweet_id}"
        logger.info(f"Tweet posted: {url}")
        return True, tweet_id, url

    def _post_thread(self, client, tweets: List[str], username: str) -> Tuple[bool, str, str]:
        """Post a chain of tweets as a thread."""
        first_id = None
        prev_id = None
        delay = self.twitter_config.get('thread_delay_seconds', 3)

        for i, tweet_text in enumerate(tweets):
            try:
                if prev_id:
                    response = client.create_tweet(
                        text=tweet_text,
                        in_reply_to_tweet_id=prev_id
                    )
                else:
                    response = client.create_tweet(text=tweet_text)

                tweet_id = str(response.data['id'])
                if first_id is None:
                    first_id = tweet_id
                prev_id = tweet_id

                if i < len(tweets) - 1:
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"Error posting thread tweet {i+1}: {e}")
                break

        if first_id:
            url = f"https://twitter.com/{username}/status/{first_id}"
            return True, first_id, url
        return False, None, "Thread posting failed"

    def process_approved_tweets(self, approved: List[Dict]) -> Dict:
        """Post all approved tweets. Returns summary."""
        results = {'posted': 0, 'failed': 0, 'skipped': 0}
        account = self.twitter_config.get('username', '')
        delay = self.twitter_config.get('post_delay_seconds', 15)

        for tweet in approved:
            draft_id = tweet.get('id')
            article_id = tweet.get('article_id')

            if article_id and article_already_tweeted(article_id):
                update_tweet_status(draft_id, 'skipped')
                results['skipped'] += 1
                continue

            thread_tweets_raw = tweet.get('thread_tweets', '[]')
            try:
                thread_tweets = json.loads(thread_tweets_raw) if isinstance(thread_tweets_raw, str) else thread_tweets_raw
            except Exception:
                thread_tweets = []

            payload = {
                'tweet_text': tweet.get('tweet_text', ''),
                'tweet_type': tweet.get('tweet_type', 'single'),
                'thread_tweets': thread_tweets
            }

            success, tid, url_or_err = self.post_tweet(payload)
            if success:
                record_posted_tweet(draft_id, tid, url_or_err, account)
                results['posted'] += 1
                logger.info(f"Tweet posted: {url_or_err}")
                if delay > 0:
                    time.sleep(delay)
            else:
                update_tweet_status(draft_id, 'failed')
                logger.error(f"Tweet failed: {url_or_err}")
                results['failed'] += 1

        return results

    def update_tweet_metrics(self) -> int:
        """Fetch updated metrics for recent tweets (requires API access)."""
        # Twitter free tier doesn't include metrics — skipping
        return 0


DEFAULT_TWITTER_INSTRUCTIONS = """
Write short, punchy tweets.
Sound like a human expert sharing a genuine observation.
Max 280 characters.
Conversational, no corporate language.
"""
