"""
Flask Web Dashboard
Provides a browser-based admin interface for the Reddit Automation System.
Access at: http://localhost:5000
"""

import json
import os
import sys
import yaml
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from modules import knowledge_base as kb
from modules.analyzer import ContentAnalyzer
from modules.reddit_client import RedditClient


def create_app(config: dict) -> Flask:
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.secret_key = config.get('dashboard', {}).get('secret_key', 'dev-secret-key-change-me')
    app.config['APP_CONFIG'] = config

    # ----------------------------------------------------------------
    # MAIN DASHBOARD
    # ----------------------------------------------------------------

    @app.route('/')
    def index():
        stats = kb.get_knowledge_base_stats()
        # Merge in storage stats
        try:
            storage = kb.get_storage_stats()
            stats['cleaned_articles'] = storage.get('articles_cleaned', 0)
            stats['db_size_mb'] = storage.get('db_size_mb', 0)
        except Exception:
            stats['cleaned_articles'] = 0
            stats['db_size_mb'] = 0
        recent_logs = kb.get_recent_logs(limit=10)
        recent_posts = kb.get_published_posts(limit=5)
        pending = kb.get_pending_posts(limit=5)
        return render_template('index.html',
                               stats=stats,
                               recent_logs=recent_logs,
                               recent_posts=recent_posts,
                               pending=pending,
                               config=config)

    # ----------------------------------------------------------------
    # SOURCES MANAGEMENT
    # ----------------------------------------------------------------

    @app.route('/sources')
    def sources():
        all_sources = kb.get_all_sources(enabled_only=False)
        # Parse tags from JSON
        for s in all_sources:
            try:
                s['tags_list'] = json.loads(s.get('tags') or '[]')
            except Exception:
                s['tags_list'] = []
        return render_template('sources.html', sources=all_sources)

    @app.route('/sources/toggle/<source_id>', methods=['POST'])
    def toggle_source(source_id):
        current = kb.get_all_sources(enabled_only=False)
        source = next((s for s in current if s['id'] == source_id), None)
        if source:
            new_state = not bool(source.get('enabled', 1))
            kb.toggle_source(source_id, new_state)
            flash(f"Source {'enabled' if new_state else 'paused'}", 'success')
        return redirect(url_for('sources'))

    @app.route('/sources/add', methods=['POST'])
    def add_source():
        """Add a new source via the dashboard (writes to sources.yaml)."""
        sources_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'sources.yaml')
        try:
            with open(sources_path, 'r') as f:
                data = yaml.safe_load(f) or {'sources': []}

            import time
            new_source = {
                'id': f"source_{int(time.time())}",
                'name': request.form.get('name', 'New Source'),
                'url': request.form.get('url', ''),
                'type': request.form.get('type', 'webpage'),
                'category': request.form.get('category', ''),
                'priority': int(request.form.get('priority', 3)),
                'enabled': True,
                'tags': [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
            }

            if not new_source['url']:
                flash('URL is required', 'error')
                return redirect(url_for('sources'))

            data['sources'].append(new_source)

            with open(sources_path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

            # Sync to database
            kb.sync_sources_from_config(data['sources'])
            flash(f"Source '{new_source['name']}' added successfully!", 'success')

        except Exception as e:
            flash(f'Error adding source: {e}', 'error')

        return redirect(url_for('sources'))

    @app.route('/sources/delete/<source_id>', methods=['POST'])
    def delete_source(source_id):
        """Disable (soft delete) a source."""
        kb.toggle_source(source_id, False)
        flash('Source disabled', 'info')
        return redirect(url_for('sources'))

    # ----------------------------------------------------------------
    # KNOWLEDGE BASE / ARTICLES
    # ----------------------------------------------------------------

    @app.route('/articles')
    def articles():
        page = request.args.get('page', 1, type=int)
        status_filter = request.args.get('status', '')
        articles_list = kb.get_new_articles(limit=50) + kb.get_articles_for_processing(limit=50)
        return render_template('articles.html', articles=articles_list[:50], status_filter=status_filter)

    # ----------------------------------------------------------------
    # POSTS REVIEW (Draft Queue)
    # ----------------------------------------------------------------

    @app.route('/posts')
    def posts():
        pending = kb.get_pending_posts(limit=50)
        for p in pending:
            try:
                p['article_title'] = p.get('article_title', 'Unknown Article')
            except Exception:
                pass
        return render_template('posts.html', posts=pending)

    @app.route('/posts/approve/<int:post_id>', methods=['POST'])
    def approve_post(post_id):
        notes = request.form.get('notes', '')
        kb.update_post_status(post_id, 'approved', notes)
        flash('Post approved for publishing!', 'success')
        return redirect(url_for('posts'))

    @app.route('/posts/reject/<int:post_id>', methods=['POST'])
    def reject_post(post_id):
        notes = request.form.get('notes', 'Rejected by reviewer')
        kb.update_post_status(post_id, 'rejected', notes)
        flash('Post rejected', 'info')
        return redirect(url_for('posts'))

    @app.route('/posts/edit/<int:post_id>', methods=['GET', 'POST'])
    def edit_post(post_id):
        post = kb.get_post_by_id(post_id)
        if not post:
            flash('Post not found', 'error')
            return redirect(url_for('posts'))

        if request.method == 'POST':
            # Update the post
            from modules.knowledge_base import get_connection
            conn = get_connection()
            try:
                conn.execute("""
                    UPDATE generated_posts SET title=?, body=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (request.form.get('title'), request.form.get('body'), post_id))
                conn.commit()
                flash('Post updated', 'success')
            finally:
                conn.close()
            return redirect(url_for('posts'))

        return render_template('edit_post.html', post=post)

    # ----------------------------------------------------------------
    # PUBLISHED POSTS
    # ----------------------------------------------------------------

    @app.route('/published')
    def published():
        posts_list = kb.get_published_posts(limit=100)
        return render_template('published.html', posts=posts_list)

    # ----------------------------------------------------------------
    # ANALYTICS
    # ----------------------------------------------------------------

    @app.route('/analytics')
    def analytics():
        data = kb.get_analytics_summary()
        return render_template('analytics.html', data=data)

    # ----------------------------------------------------------------
    # SETTINGS
    # ----------------------------------------------------------------

    @app.route('/settings')
    def settings():
        settings_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
        instructions_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'writing_instructions.md')
        subreddits_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'subreddits.yaml')

        settings_content = ''
        instructions_content = ''
        subreddits_content = ''

        try:
            with open(settings_path, 'r') as f:
                settings_content = f.read()
        except Exception:
            pass

        try:
            with open(instructions_path, 'r') as f:
                instructions_content = f.read()
        except Exception:
            pass

        try:
            with open(subreddits_path, 'r') as f:
                subreddits_content = f.read()
        except Exception:
            pass

        # Check system status
        reddit_status = {'connected': False}
        ollama_status = {'running': False}
        try:
            reddit_client = RedditClient(config)
            reddit_status = reddit_client.test_connection()
        except Exception:
            pass

        try:
            analyzer = ContentAnalyzer(config)
            if config.get('ai', {}).get('provider') == 'ollama':
                ollama_status = analyzer.check_ollama_status()
        except Exception:
            pass

        return render_template('settings.html',
                               settings_content=settings_content,
                               instructions_content=instructions_content,
                               subreddits_content=subreddits_content,
                               reddit_status=reddit_status,
                               ollama_status=ollama_status,
                               ai_config=config.get('ai', {}))

    @app.route('/settings/save-instructions', methods=['POST'])
    def save_instructions():
        instructions_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'writing_instructions.md')
        content = request.form.get('content', '')
        try:
            with open(instructions_path, 'w') as f:
                f.write(content)
            flash('Writing instructions saved!', 'success')
        except Exception as e:
            flash(f'Error saving: {e}', 'error')
        return redirect(url_for('settings'))

    @app.route('/settings/save-subreddits', methods=['POST'])
    def save_subreddits():
        subreddits_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'subreddits.yaml')
        content = request.form.get('content', '')
        try:
            # Validate YAML
            yaml.safe_load(content)
            with open(subreddits_path, 'w') as f:
                f.write(content)
            flash('Subreddits configuration saved!', 'success')
        except yaml.YAMLError as e:
            flash(f'Invalid YAML: {e}', 'error')
        except Exception as e:
            flash(f'Error saving: {e}', 'error')
        return redirect(url_for('settings'))

    @app.route('/settings/save-settings', methods=['POST'])
    def save_settings():
        settings_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
        content = request.form.get('content', '')
        try:
            yaml.safe_load(content)
            with open(settings_path, 'w') as f:
                f.write(content)
            flash('Settings saved! Restart the bot to apply changes.', 'success')
        except yaml.YAMLError as e:
            flash(f'Invalid YAML: {e}', 'error')
        except Exception as e:
            flash(f'Error saving: {e}', 'error')
        return redirect(url_for('settings'))

    # ----------------------------------------------------------------
    # LOGS
    # ----------------------------------------------------------------

    @app.route('/logs')
    def logs():
        level_filter = request.args.get('level', '')
        log_entries = kb.get_recent_logs(limit=200, level=level_filter if level_filter else None)
        return render_template('logs.html', logs=log_entries, level_filter=level_filter)

    # ----------------------------------------------------------------
    # API ENDPOINTS (for AJAX)
    # ----------------------------------------------------------------

    @app.route('/api/stats')
    def api_stats():
        stats = kb.get_knowledge_base_stats()
        try:
            storage = kb.get_storage_stats()
            stats['cleaned_articles'] = storage.get('articles_cleaned', 0)
            stats['db_size_mb'] = storage.get('db_size_mb', 0)
        except Exception:
            pass
        return jsonify(stats)

    @app.route('/api/run-cleanup', methods=['POST'])
    def api_run_cleanup():
        """Manually trigger storage cleanup."""
        days = config.get('storage', {}).get('cleanup_after_days', 2)
        try:
            cleaned = kb.cleanup_old_content(days_old=days)
            if cleaned > 0:
                kb.log_to_db('INFO', 'cleanup', f'Manual cleanup: removed content from {cleaned} article(s)')
            return jsonify({'status': 'ok', 'cleaned': cleaned, 'days': days})
        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e), 'cleaned': 0})

    # ----------------------------------------------------------------
    # TWITTER ROUTES
    # ----------------------------------------------------------------

    @app.route('/twitter')
    def twitter():
        from modules.twitter_client import get_pending_tweets, get_published_tweets
        pending = get_pending_tweets(limit=50)
        published = get_published_tweets(limit=50)
        twitter_cfg = config.get('twitter', {})
        return render_template('twitter.html',
                               pending=pending,
                               published=published,
                               twitter_enabled=twitter_cfg.get('enabled', False),
                               twitter_cfg=twitter_cfg)

    @app.route('/twitter/approve/<int:tweet_id>', methods=['POST'])
    def approve_tweet(tweet_id):
        from modules.twitter_client import update_tweet_status
        update_tweet_status(tweet_id, 'approved')
        flash('Tweet approved for posting!', 'success')
        return redirect(url_for('twitter'))

    @app.route('/twitter/reject/<int:tweet_id>', methods=['POST'])
    def reject_tweet(tweet_id):
        from modules.twitter_client import update_tweet_status
        update_tweet_status(tweet_id, 'rejected')
        flash('Tweet rejected', 'info')
        return redirect(url_for('twitter'))

    # ----------------------------------------------------------------
    # QUORA ROUTES
    # ----------------------------------------------------------------

    @app.route('/quora')
    def quora():
        from modules.quora_generator import get_all_quora_drafts
        drafts = get_all_quora_drafts(limit=100)
        quora_cfg = config.get('quora', {})
        return render_template('quora.html',
                               drafts=drafts,
                               quora_enabled=quora_cfg.get('enabled', False),
                               quora_cfg=quora_cfg)

    @app.route('/quora/export/<int:draft_id>', methods=['POST'])
    def export_quora_draft(draft_id):
        from modules.quora_generator import get_quora_draft_by_id, QuoraGenerator, update_quora_draft_status
        draft = get_quora_draft_by_id(draft_id)
        if draft:
            try:
                gen = QuoraGenerator(config)
                path = gen.export_draft_to_file(draft)
                update_quora_draft_status(draft_id, 'exported')
                flash(f'Draft exported to quora_drafts/ folder', 'success')
            except Exception as e:
                flash(f'Export error: {e}', 'error')
        return redirect(url_for('quora'))

    @app.route('/quora/mark-posted/<int:draft_id>', methods=['POST'])
    def mark_quora_posted(draft_id):
        from modules.quora_generator import update_quora_draft_status
        update_quora_draft_status(draft_id, 'posted_manually')
        flash('Marked as posted on Quora', 'success')
        return redirect(url_for('quora'))

    @app.route('/api/status')
    def api_status():
        reddit_status = {'connected': False}
        ollama_status = {'running': False}
        try:
            reddit_client = RedditClient(config)
            reddit_status = reddit_client.test_connection()
        except Exception:
            pass
        try:
            analyzer = ContentAnalyzer(config)
            if config.get('ai', {}).get('provider') == 'ollama':
                ollama_status = analyzer.check_ollama_status()
        except Exception:
            pass

        return jsonify({
            'reddit': reddit_status,
            'ollama': ollama_status,
            'timestamp': datetime.utcnow().isoformat()
        })

    @app.route('/api/run-cycle', methods=['POST'])
    def api_run_cycle():
        """Trigger a manual automation cycle."""
        import threading
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

        def run_in_background():
            from main import run_full_cycle
            run_full_cycle(config)

        thread = threading.Thread(target=run_in_background, daemon=True)
        thread.start()
        return jsonify({'status': 'started', 'message': 'Automation cycle started in background'})

    # Template filter for datetime formatting
    @app.template_filter('format_datetime')
    def format_datetime(value):
        if not value:
            return 'Never'
        try:
            if isinstance(value, str):
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                dt = value
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return str(value)

    @app.template_filter('truncate_text')
    def truncate_text(value, length=100):
        if not value:
            return ''
        return str(value)[:length] + ('...' if len(str(value)) > length else '')

    return app
