# 🤖 Reddit Automation System

An AI-powered content monitoring, knowledge management, and Reddit publishing system.
**100% free. No cloud subscriptions. Runs on your computer.**

---

## What This System Does

```
Your Websites/Blogs/RSS Feeds
        ↓
Automatically checks for new content every 30 minutes
        ↓
Scrapes & stores articles in a local database
        ↓
AI (running locally on your machine) reads and analyzes each article
        ↓
Generates original, human-sounding Reddit discussion posts
        ↓
You review & approve (optional) → Posts to Reddit automatically
        ↓
Tracks upvotes, comments, and performance over time
```

---

## Requirements (All Free)

| What | Why | Cost |
|------|-----|------|
| **Python 3.8+** | Runs the bot | Free |
| **Ollama** | Local AI (no API key needed) | Free |
| **Reddit account** | For posting | Free |
| **Reddit API app** | API access | Free |

---

## Quick Start (5 Steps)

### Step 1 — Run Setup
```bash
cd reddit_bot
python setup.py
```

### Step 2 — Install Ollama (Free Local AI)
1. Download from **https://ollama.ai**
2. Install and open it
3. Open Terminal and run:
```bash
ollama serve
ollama pull llama3.1
```
*This downloads the AI model (~5GB). One-time download.*

### Step 3 — Get Reddit API Credentials (Free)
1. Log into Reddit
2. Go to **https://www.reddit.com/prefs/apps**
3. Click **"Create App"** at the bottom
4. Fill in:
   - **Name**: anything (e.g. "MyBot")
   - **Type**: select **"script"**
   - **Redirect URI**: `http://localhost:8080`
5. Click Create
6. You'll see: **client_id** (14-character code under app name) and **client_secret**

### Step 4 — Add Your Credentials
Open `config/settings.yaml` and fill in:
```yaml
reddit:
  client_id: "paste your client_id here"
  client_secret: "paste your client_secret here"
  username: "your_reddit_username"
  password: "your_reddit_password"
```

### Step 5 — Add Your Sources & Start
1. Open `config/sources.yaml` — add your blog/RSS URLs
2. Open `config/subreddits.yaml` — set your target subreddits

Then run:
```bash
python main.py --status      # Check everything is working
python main.py --dashboard   # Open the web dashboard at http://localhost:5000
python main.py               # Start the full automation
```

---

## Running the Bot

| Command | What it does |
|---------|-------------|
| `python main.py` | Start full continuous automation |
| `python main.py --once` | Run one cycle and stop |
| `python main.py --status` | Check system status |
| `python main.py --dashboard` | Open web dashboard only |
| `python main.py --monitor-only` | Only scrape new content |
| `python main.py --analyze-only` | Only run AI analysis |
| `python main.py --generate-only` | Only generate posts |
| `python main.py --publish-only` | Only publish approved posts |

---

## Web Dashboard

When the bot is running, open your browser to:
**http://localhost:5000**

From the dashboard you can:
- **Dashboard** — See stats, run a cycle manually, review recent activity
- **Sources** — Add/remove/pause monitored websites
- **Articles** — Browse the knowledge base
- **Review Queue** — Approve or reject AI-generated posts
- **Published** — See all published posts with performance data
- **Analytics** — Charts and statistics
- **Settings** — Edit writing instructions, subreddits, and config
- **Logs** — View system activity and errors

---

## Approval Modes

Set in `config/settings.yaml` → `posting.approval_mode`

| Mode | Behavior |
|------|----------|
| **A** | Fully automatic — new content → generate → post immediately |
| **B** (default) | Review first — generate → save draft → you approve → post |
| **C** | Suggest only — generate drafts, never post automatically |

**Recommended for beginners:** Start with Mode B to review posts before they go live.

---

## Customizing Writing Style

Edit `config/writing_instructions.md` — the AI reads this file fresh before every post generation.

Change:
- Tone of voice
- Post structure
- What to avoid
- Examples of good/bad posts
- Subreddit-specific instructions

No restart needed — changes apply immediately on the next run.

---

## File Structure

```
reddit_bot/
├── config/
│   ├── settings.yaml          ← Main config (API keys, intervals)
│   ├── sources.yaml           ← Websites and RSS feeds to monitor
│   ├── subreddits.yaml        ← Target subreddits
│   └── writing_instructions.md ← AI writing style (edit anytime)
├── database/
│   ├── schema.sql             ← Database structure
│   └── knowledge_base.db      ← Your data (created on first run)
├── modules/
│   ├── knowledge_base.py      ← Database operations
│   ├── monitor.py             ← Web scraping & RSS monitoring
│   ├── analyzer.py            ← AI content analysis
│   ├── generator.py           ← Reddit post generation
│   ├── reddit_client.py       ← Reddit API integration
│   └── performance_tracker.py ← Metrics tracking
├── dashboard/
│   ├── app.py                 ← Flask web dashboard
│   └── templates/             ← Dashboard HTML pages
├── logs/
│   └── system.log             ← Activity log
├── main.py                    ← Run this to start the bot
├── setup.py                   ← Run this once to install
├── requirements.txt           ← Python package list
└── README.md                  ← This file
```

---

## Adding Sources

Edit `config/sources.yaml`. Example formats:

```yaml
# RSS Feed (most reliable)
- id: "my_blog_001"
  name: "My Blog"
  url: "https://myblog.com/feed"
  type: "rss"
  category: "Technology"
  priority: 1
  enabled: true
  tags: ["tech", "ai"]

# Wix Blog
- id: "wix_blog_001"
  name: "My Wix Site"
  url: "https://mysite.wixsite.com/blog"
  type: "wix_blog"
  category: "Business"
  priority: 1
  enabled: true
  tags: ["business"]

# Regular Webpage
- id: "news_site_001"
  name: "Tech News"
  url: "https://technews.com/latest/"
  type: "webpage"
  category: "News"
  priority: 2
  enabled: true
  tags: ["news"]
```

**Finding RSS feeds:** Most blogs have one. Try:
- `https://yourblog.com/feed`
- `https://yourblog.com/feed.xml`
- `https://yourblog.com/rss`
- WordPress blogs: add `/feed` to any URL

---

## Troubleshooting

### "Cannot connect to Ollama"
- Make sure Ollama is running: open Terminal, type `ollama serve`
- Make sure you downloaded the model: `ollama pull llama3.1`

### "Reddit authentication failed"
- Double-check your `client_id`, `client_secret`, `username`, `password` in `settings.yaml`
- Make sure your Reddit app type is set to "script" (not "web app")
- Try logging into Reddit normally to confirm your password is correct

### "No new articles found"
- Check that your source URLs are correct
- Try visiting the URL in your browser
- For RSS feeds, paste the URL into an RSS reader to test it
- Check the Logs page in the dashboard for details

### Bot posts too slowly / not posting
- Check `posting.approval_mode` in settings.yaml — if set to B, you need to approve posts first
- Visit the dashboard Review Queue to approve posts
- Check the Logs for any errors

---

## Privacy & Security

- All data stays on your computer (SQLite database, local AI)
- Reddit credentials stored only in `config/settings.yaml` (keep this file private)
- Never share your `settings.yaml` or commit it to public repositories
- The web dashboard is only accessible from your computer (localhost:5000)

---

## Future Expansion

The system is designed to be extended. Planned modules:
- **Quora automation** — answer questions from the knowledge base
- **LinkedIn posts** — professional content generation
- **Twitter/X posts** — short-form content
- **Blog generation** — full blog posts from aggregated knowledge
- **Comment automation** — engage in Reddit discussions
- **Trend detection** — identify emerging topics

---

*Built for non-coders: no cloud, no subscriptions, no API costs.*
