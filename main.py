"""
Multi-Platform Content Automation System - Main Orchestrator
=============================================================
Coordinates the full pipeline across Reddit, Twitter/X, and Quora:
  1. Content monitoring (scan sources for new articles)
  2. AI analysis (analyze and score articles)
  3. Reddit post generation & publishing
  4. Twitter/X tweet generation & posting
  5. Quora answer generation & export
  6. Performance tracking
  7. Storage cleanup

Run this file to start the bot:
  python main.py              → Start full automated system
  python main.py --once       → Run one cycle and exit
  python main.py --dashboard  → Start web dashboard only
  python main.py --status     → Show system status
"""

import argparse
import logging
import os
import sys
import time
import json
import yaml
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules import knowledge_base as kb
from modules.monitor import run_monitoring_cycle
from modules.analyzer import ContentAnalyzer
from modules.generator import PostGenerator
from modules.reddit_client import RedditClient
from modules.performance_tracker import PerformanceTracker
from modules.twitter_client import TweetGenerator, TwitterClient, save_twitter_draft, get_approved_tweets
from modules.quora_generator import QuoraGenerator, save_quora_draft, get_quora_drafts


# ----------------------------------------------------------------
# CONFIGURATION LOADING
# ----------------------------------------------------------------

def load_config(config_path: str = None) -> Dict:
    """Load main settings.yaml configuration."""
    if not config_path:
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'settings.yaml')
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"ERROR: Config file not found at {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"ERROR: Invalid YAML in config: {e}")
        sys.exit(1)


def load_sources_config() -> List[Dict]:
    """Load sources.yaml configuration."""
    sources_path = os.path.join(os.path.dirname(__file__), 'config', 'sources.yaml')
    try:
        with open(sources_path, 'r') as f:
            data = yaml.safe_load(f)
        return data.get('sources', [])
    except Exception as e:
        logger.error(f"Could not load sources.yaml: {e}")
        return []


def load_subreddits_config() -> List[Dict]:
    """Load subreddits.yaml configuration."""
    subreddits_path = os.path.join(os.path.dirname(__file__), 'config', 'subreddits.yaml')
    try:
        with open(subreddits_path, 'r') as f:
            data = yaml.safe_load(f)
        return data.get('subreddits', [])
    except Exception as e:
        logger.error(f"Could not load subreddits.yaml: {e}")
        return []


# ----------------------------------------------------------------
# LOGGING SETUP
# ----------------------------------------------------------------

def setup_logging(config: Dict):
    """Configure logging to both file and console."""
    log_config = config.get('logging', {})
    log_level = getattr(logging, log_config.get('level', 'INFO'), logging.INFO)
    log_file = os.path.join(
        os.path.dirname(__file__),
        log_config.get('log_file', 'logs/system.log')
    )

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Format
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # File handler (rotating)
    max_bytes = log_config.get('max_log_size_mb', 10) * 1024 * 1024
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=log_config.get('backup_count', 5)
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------
# MAIN PIPELINE STEPS
# ----------------------------------------------------------------

def step_monitor(config: Dict) -> int:
    """Step 1: Monitor sources and collect new articles."""
    sources = load_sources_config()

    # Sync source config to database
    kb.sync_sources_from_config(sources)

    max_per_source = config.get('monitoring', {}).get('max_articles_per_scan', 10)
    result = run_monitoring_cycle(config, max_per_source=max_per_source)

    kb.log_to_db('INFO', 'monitor', 'Monitoring cycle complete', result)
    return result.get('new_articles', 0)


def step_analyze(config: Dict) -> int:
    """Step 2: Run AI analysis on newly scraped articles."""
    analyzer = ContentAnalyzer(config)
    articles = kb.get_new_articles(limit=20)

    if not articles:
        logger.info("No new articles to analyze")
        return 0

    logger.info(f"Analyzing {len(articles)} new article(s)...")
    analyzed_count = 0

    for article in articles:
        try:
            analysis = analyzer.analyze_article(article)
            if analysis:
                kb.save_article_analysis(article['id'], analysis)
                analyzed_count += 1
                logger.info(f"  ✓ Analyzed: {article['title'][:60]}")
            else:
                kb.mark_article_status(article['id'], 'error', 'AI analysis returned no result')
                logger.warning(f"  ✗ Analysis failed: {article['title'][:60]}")
        except Exception as e:
            logger.error(f"Error analyzing article {article['id']}: {e}")
            kb.mark_article_status(article['id'], 'error', str(e))

    logger.info(f"Analysis complete: {analyzed_count}/{len(articles)} articles analyzed")
    kb.log_to_db('INFO', 'analyzer', f'Analyzed {analyzed_count} articles')
    return analyzed_count


def step_generate(config: Dict) -> int:
    """Step 3: Generate Reddit posts from analyzed articles."""
    approval_mode = config.get('posting', {}).get('approval_mode', 'B')
    subreddits = load_subreddits_config()

    if not subreddits:
        logger.warning("No subreddits configured. Edit config/subreddits.yaml")
        return 0

    analyzer = ContentAnalyzer(config)
    generator = PostGenerator(config, analyzer=analyzer)
    articles = kb.get_articles_for_processing(limit=10)

    if not articles:
        logger.info("No analyzed articles ready for post generation")
        return 0

    logger.info(f"Generating posts for {len(articles)} article(s)...")
    generated_count = 0

    for article in articles:
        try:
            # Parse stored JSON fields
            analysis = {
                'summary': article.get('summary', ''),
                'key_insights': json.loads(article.get('key_insights') or '[]'),
                'major_topics': json.loads(article.get('major_topics') or '[]'),
                'trends': json.loads(article.get('trends') or '[]'),
                'discussion_opportunities': json.loads(article.get('discussion_opportunities') or '[]'),
                'relevance_score': article.get('relevance_score', 0.5),
                'best_post_angle': ''
            }

            # Generate one post per enabled subreddit
            posts = generator.generate_variations(article, analysis, subreddits)

            for post in posts:
                # Set initial status based on approval mode
                if approval_mode == 'A':
                    post_status = 'approved'  # Auto mode: immediately approved
                elif approval_mode == 'B':
                    post_status = 'draft'     # Review mode: needs human approval
                else:
                    post_status = 'draft'     # Suggest mode: stored only

                post['status'] = post_status
                post['approval_mode'] = approval_mode

                post_id = kb.save_generated_post(post)
                if post_id:
                    generated_count += 1
                    logger.info(f"  ✓ Generated post for r/{post['subreddit']}: {post['title'][:50]}")

            # Mark article as processed
            if posts:
                kb.mark_article_status(article['id'], 'generated')
            else:
                kb.mark_article_status(article['id'], 'skipped')

        except Exception as e:
            logger.error(f"Error generating posts for article {article['id']}: {e}")
            kb.mark_article_status(article['id'], 'error', str(e))

    logger.info(f"Post generation complete: {generated_count} posts created")
    kb.log_to_db('INFO', 'generator', f'Generated {generated_count} posts', {'mode': approval_mode})
    return generated_count


def step_publish(config: Dict) -> int:
    """Step 4: Publish approved posts to Reddit."""
    approval_mode = config.get('posting', {}).get('approval_mode', 'B')

    # Mode C = suggest only, never publish
    if approval_mode == 'C':
        logger.info("Approval mode C: posts are for review only, skipping publishing")
        return 0

    # Mode B = only publish explicitly approved posts
    # Mode A = posts are pre-approved in generate step

    approved_posts = kb.get_approved_posts()

    if not approved_posts:
        logger.info("No approved posts ready to publish")
        return 0

    reddit = RedditClient(config)
    connection = reddit.test_connection()

    if not connection.get('connected'):
        logger.error(f"Cannot publish: Reddit not connected. {connection.get('error', '')}")
        logger.error("Check your Reddit credentials in config/settings.yaml")
        return 0

    logger.info(f"Publishing {len(approved_posts)} approved post(s)...")
    results = reddit.process_approved_posts(approved_posts)

    logger.info(
        f"Publishing complete: {results['published']} published, "
        f"{results['failed']} failed, {results['skipped']} skipped"
    )
    kb.log_to_db('INFO', 'publisher', 'Publishing cycle complete', results)
    return results.get('published', 0)


def step_track_performance(config: Dict):
    """Step 5: Update performance metrics for published posts."""
    reddit_client = RedditClient(config)
    tracker = PerformanceTracker(reddit_client=reddit_client)
    tracker.run_tracking_cycle()


def step_twitter(config: Dict) -> Dict:
    """
    Twitter pipeline: generate tweets for analyzed articles,
    then publish approved ones.
    Returns {'generated': N, 'published': N}
    """
    if not config.get('twitter', {}).get('enabled', False):
        return {'generated': 0, 'published': 0, 'skipped': True}

    approval_mode = config.get('twitter', {}).get('approval_mode', 'B')
    analyzer = ContentAnalyzer(config)
    generator = TweetGenerator(config, analyzer=analyzer)
    articles = kb.get_articles_for_processing(limit=10)
    generated = 0

    for article in articles:
        try:
            analysis = {
                'summary': article.get('summary', ''),
                'key_insights': json.loads(article.get('key_insights') or '[]'),
                'major_topics': json.loads(article.get('major_topics') or '[]'),
                'best_post_angle': '',
                'discussion_opportunities': json.loads(article.get('discussion_opportunities') or '[]'),
            }
            tweet = generator.generate_tweet(article, analysis)
            if tweet:
                status = 'approved' if approval_mode == 'A' else 'draft'
                tweet['status'] = status
                tweet['approval_mode'] = approval_mode
                save_twitter_draft(tweet)
                generated += 1
                logger.info(f"  ✓ Tweet generated for: {article['title'][:50]}")
        except Exception as e:
            logger.error(f"Twitter generation error for article {article['id']}: {e}")

    # Publish approved tweets
    published = 0
    if approval_mode != 'C':
        approved = get_approved_tweets()
        if approved:
            client = TwitterClient(config)
            conn_check = client.test_connection()
            if conn_check.get('connected'):
                results_pub = client.process_approved_tweets(approved)
                published = results_pub.get('posted', 0)
            else:
                logger.warning(f"Twitter not connected: {conn_check.get('error')}")

    kb.log_to_db('INFO', 'twitter', f'Twitter: {generated} generated, {published} posted')
    return {'generated': generated, 'published': published}


def step_quora(config: Dict) -> Dict:
    """
    Quora pipeline: generate answers for analyzed articles
    and export them as ready-to-paste draft files.
    Returns {'generated': N, 'exported': N}
    """
    if not config.get('quora', {}).get('enabled', False):
        return {'generated': 0, 'exported': 0, 'skipped': True}

    max_per_cycle = config.get('quora', {}).get('max_answers_per_cycle', 3)
    min_relevance = config.get('quora', {}).get('min_article_relevance', 0.5)
    analyzer = ContentAnalyzer(config)
    generator = QuoraGenerator(config, analyzer=analyzer)
    articles = kb.get_articles_for_processing(limit=20)

    # Filter by relevance threshold
    articles = [
        a for a in articles
        if (a.get('relevance_score') or 0) >= min_relevance
    ][:max_per_cycle]

    generated = 0
    exported = 0

    for article in articles:
        try:
            analysis = {
                'summary': article.get('summary', ''),
                'key_insights': json.loads(article.get('key_insights') or '[]'),
                'major_topics': json.loads(article.get('major_topics') or '[]'),
                'discussion_opportunities': json.loads(article.get('discussion_opportunities') or '[]'),
            }
            draft = generator.generate_answer(article, analysis)
            if draft:
                draft_id = save_quora_draft(draft)
                generated += 1
                logger.info(f"  ✓ Quora answer generated: {draft['question'][:60]}")

                # Auto-export to file if enabled
                if config.get('quora', {}).get('auto_export_drafts', True) and draft_id:
                    draft['id'] = draft_id
                    generator.export_draft_to_file(draft)
                    exported += 1

        except Exception as e:
            logger.error(f"Quora generation error for article {article['id']}: {e}")

    kb.log_to_db('INFO', 'quora', f'Quora: {generated} generated, {exported} exported')
    return {'generated': generated, 'exported': exported}


def step_cleanup_storage(config: Dict) -> int:
    """
    Storage cleanup: delete scraped content from articles that were posted
    more than N days ago. Keeps metadata for duplicate prevention.
    Default: 2 days after posting.
    """
    days = config.get('storage', {}).get('cleanup_after_days', 2)
    if days == 0:
        return 0
    cleaned = kb.cleanup_old_content(days_old=days)
    if cleaned > 0:
        kb.log_to_db(
            'INFO', 'cleanup',
            f'Storage cleanup: removed content from {cleaned} article(s) older than {days} day(s)'
        )
    return cleaned


# ----------------------------------------------------------------
# FULL AUTOMATION CYCLE
# ----------------------------------------------------------------

def run_full_cycle(config: Dict) -> Dict:
    """Run one complete pipeline cycle across all enabled platforms."""
    start_time = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("STARTING AUTOMATION CYCLE")
    logger.info("=" * 60)

    results = {}

    twitter_enabled = config.get('twitter', {}).get('enabled', False)
    quora_enabled = config.get('quora', {}).get('enabled', False)
    total_steps = 6 + (1 if twitter_enabled else 0) + (1 if quora_enabled else 0)
    step = [0]

    def next_step(label):
        step[0] += 1
        logger.info(f"\n[{step[0]}/{total_steps}] {label}")

    try:
        next_step("Monitoring sources for new content...")
        results['new_articles'] = step_monitor(config)

        if results['new_articles'] > 0:
            next_step(f"Analyzing {results['new_articles']} new article(s) with AI...")
            results['analyzed'] = step_analyze(config)
        else:
            next_step("No new articles — skipping analysis")
            results['analyzed'] = 0

        # ── Reddit ──
        next_step("Reddit: Generating posts...")
        results['reddit_generated'] = step_generate(config)

        next_step("Reddit: Publishing approved posts...")
        results['reddit_published'] = step_publish(config)

        # ── Twitter/X ──
        if twitter_enabled:
            next_step("Twitter/X: Generating & publishing tweets...")
            tw = step_twitter(config)
            results['twitter_generated'] = tw.get('generated', 0)
            results['twitter_published'] = tw.get('published', 0)
            logger.info(f"  → Generated: {results['twitter_generated']}  Published: {results['twitter_published']}")

        # ── Quora ──
        if quora_enabled:
            next_step("Quora: Generating answer drafts...")
            qr = step_quora(config)
            results['quora_generated'] = qr.get('generated', 0)
            results['quora_exported'] = qr.get('exported', 0)
            if results['quora_exported'] > 0:
                logger.info(f"  → {results['quora_exported']} answer(s) saved to quora_drafts/ folder")

        # ── Performance & Cleanup ──
        next_step("Tracking Reddit post performance...")
        step_track_performance(config)

        next_step("Cleaning up old scraped content...")
        results['cleaned'] = step_cleanup_storage(config)
        days = config.get('storage', {}).get('cleanup_after_days', 2)
        if results['cleaned'] > 0:
            logger.info(f"  → Cleared content from {results['cleaned']} article(s) (>{days} days old)")
        else:
            logger.info(f"  → Nothing to clean yet (threshold: {days} days after posting)")

    except Exception as e:
        logger.error(f"Error in automation cycle: {e}", exc_info=True)
        kb.log_to_db('ERROR', 'orchestrator', f'Cycle error: {e}')

    elapsed = (datetime.utcnow() - start_time).seconds
    results['elapsed_seconds'] = elapsed

    logger.info("\n" + "=" * 60)
    logger.info(f"CYCLE COMPLETE in {elapsed}s")
    logger.info(f"  New articles:      {results.get('new_articles', 0)}")
    logger.info(f"  Analyzed:          {results.get('analyzed', 0)}")
    logger.info(f"  Reddit posts:      {results.get('reddit_generated', 0)} created / {results.get('reddit_published', 0)} published")
    if twitter_enabled:
        logger.info(f"  Tweets:            {results.get('twitter_generated', 0)} created / {results.get('twitter_published', 0)} posted")
    if quora_enabled:
        logger.info(f"  Quora answers:     {results.get('quora_generated', 0)} generated / {results.get('quora_exported', 0)} exported")
    logger.info(f"  Storage cleaned:   {results.get('cleaned', 0)} articles")
    logger.info("=" * 60)

    return results


# ----------------------------------------------------------------
# STATUS DISPLAY
# ----------------------------------------------------------------

def show_status(config: Dict):
    """Display current system status."""
    print("\n" + "=" * 60)
    print("  REDDIT AUTOMATION SYSTEM - STATUS")
    print("=" * 60)

    # Database stats
    try:
        stats = kb.get_knowledge_base_stats()
        print(f"\n  📊 Knowledge Base:")
        print(f"     Articles:       {stats.get('total_articles', 0)}")
        print(f"     Analyzed:       {stats.get('analyzed_articles', 0)}")
        print(f"     Active sources: {stats.get('total_sources', 0)}")
        print(f"     Posts published:{stats.get('total_posts', 0)}")
        print(f"     Pending review: {stats.get('pending_drafts', 0)}")
        print(f"     Last 24h:       {stats.get('recent_articles', 0)} new articles")
    except Exception as e:
        print(f"  ⚠️  Database error: {e}")

    # Reddit connection
    print(f"\n  🔗 Reddit Connection:")
    try:
        reddit = RedditClient(config)
        conn = reddit.test_connection()
        if conn.get('connected'):
            print(f"     ✅ Connected as u/{conn.get('username')}")
            print(f"     Karma: {conn.get('karma', 0):,}")
        else:
            print(f"     ❌ Not connected: {conn.get('error', 'Unknown')}")
    except Exception as e:
        print(f"     ❌ Error: {e}")

    # AI status
    print(f"\n  🤖 AI / LLM:")
    try:
        analyzer = ContentAnalyzer(config)
        if config.get('ai', {}).get('provider') == 'ollama':
            status = analyzer.check_ollama_status()
            if status.get('running'):
                model_ok = "✅" if status.get('model_available') else "⚠️ "
                print(f"     ✅ Ollama running")
                print(f"     {model_ok} Model '{analyzer.model}': {'available' if status.get('model_available') else 'NOT downloaded yet'}")
                if not status.get('model_available'):
                    print(f"        Run: ollama pull {analyzer.model}")
            else:
                print(f"     ❌ Ollama not running")
                print(f"        Install from: https://ollama.ai")
                print(f"        Then run: ollama serve")
        else:
            provider = config.get('ai', {}).get('provider', 'unknown')
            print(f"     Provider: {provider} (configured)")
    except Exception as e:
        print(f"     ❌ Error: {e}")

    # Approval mode
    mode = config.get('posting', {}).get('approval_mode', 'B')
    mode_names = {'A': 'Fully Automatic', 'B': 'Review First', 'C': 'Suggest Only'}
    print(f"\n  ⚙️  Settings:")
    print(f"     Approval mode:  {mode} ({mode_names.get(mode, mode)})")
    print(f"     Check interval: {config.get('monitoring', {}).get('check_interval_minutes', 30)} minutes")
    print(f"     Max posts/day:  {config.get('posting', {}).get('max_posts_per_day', 10)}")

    print("\n" + "=" * 60 + "\n")


# ----------------------------------------------------------------
# ENTRY POINT
# ----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Reddit Automation System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                  Start continuous automation
  python main.py --once           Run one cycle and exit
  python main.py --dashboard      Open web dashboard only
  python main.py --status         Show system status
  python main.py --analyze-only   Only analyze unprocessed articles
  python main.py --generate-only  Only generate posts from analyzed articles
        """
    )
    parser.add_argument('--once', action='store_true', help='Run one cycle then exit')
    parser.add_argument('--dashboard', action='store_true', help='Start web dashboard only')
    parser.add_argument('--status', action='store_true', help='Show status and exit')
    parser.add_argument('--monitor-only', action='store_true', help='Only run content monitoring')
    parser.add_argument('--analyze-only', action='store_true', help='Only run AI analysis')
    parser.add_argument('--generate-only', action='store_true', help='Only generate posts')
    parser.add_argument('--publish-only', action='store_true', help='Only publish approved posts')
    parser.add_argument('--config', default=None, help='Path to settings.yaml')
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Setup logging
    setup_logging(config)

    # Initialize database
    try:
        kb.initialize_database()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        sys.exit(1)

    # Handle specific modes
    if args.status:
        show_status(config)
        return

    if args.dashboard:
        logger.info("Starting web dashboard...")
        from dashboard.app import create_app
        app = create_app(config)
        dashboard_config = config.get('dashboard', {})
        app.run(
            host=dashboard_config.get('host', '127.0.0.1'),
            port=dashboard_config.get('port', 5000),
            debug=dashboard_config.get('debug', False)
        )
        return

    if args.monitor_only:
        step_monitor(config)
        return

    if args.analyze_only:
        step_analyze(config)
        return

    if args.generate_only:
        step_generate(config)
        return

    if args.publish_only:
        step_publish(config)
        return

    if args.once:
        run_full_cycle(config)
        return

    # Continuous mode: run cycles on schedule
    interval_minutes = config.get('monitoring', {}).get('check_interval_minutes', 30)
    logger.info(f"Starting continuous automation (checking every {interval_minutes} minutes)")
    logger.info("Press Ctrl+C to stop")
    logger.info(f"Dashboard available at http://{config.get('dashboard', {}).get('host', '127.0.0.1')}:{config.get('dashboard', {}).get('port', 5000)}")

    # Start dashboard in background thread
    try:
        import threading
        from dashboard.app import create_app
        app = create_app(config)
        dashboard_config = config.get('dashboard', {})
        dashboard_thread = threading.Thread(
            target=lambda: app.run(
                host=dashboard_config.get('host', '127.0.0.1'),
                port=dashboard_config.get('port', 5000),
                debug=False,
                use_reloader=False
            ),
            daemon=True
        )
        dashboard_thread.start()
        logger.info(f"Dashboard started at http://{dashboard_config.get('host', '127.0.0.1')}:{dashboard_config.get('port', 5000)}")
    except Exception as e:
        logger.warning(f"Could not start dashboard: {e}. Bot will still run.")

    # Run continuous loop
    try:
        while True:
            run_full_cycle(config)
            logger.info(f"Sleeping {interval_minutes} minutes until next cycle...")
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        logger.info("\nBot stopped by user. Goodbye!")


if __name__ == '__main__':
    main()
