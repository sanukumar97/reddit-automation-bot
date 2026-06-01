-- ============================================================
-- REDDIT AUTOMATION SYSTEM - DATABASE SCHEMA
-- SQLite database: knowledge_base.db
-- ============================================================

-- ------------------------------------------------------------
-- SOURCES TABLE: All monitored URLs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,       -- rss, webpage, wix_blog, news
    category        TEXT,
    priority        INTEGER DEFAULT 3,
    enabled         INTEGER DEFAULT 1,   -- 1=active, 0=paused
    tags            TEXT,               -- JSON array of tags
    last_checked    TIMESTAMP,
    last_new_content TIMESTAMP,
    total_articles  INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- ARTICLES TABLE: Knowledge base - all scraped content
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    content         TEXT,
    summary         TEXT,
    images          TEXT,               -- JSON array of image URLs
    tags            TEXT,               -- JSON array of tags
    author          TEXT,
    published_at    TIMESTAMP,
    discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    category        TEXT,
    source_website  TEXT,
    word_count      INTEGER DEFAULT 0,

    -- AI Analysis results
    key_insights    TEXT,               -- JSON array
    major_topics    TEXT,               -- JSON array
    trends          TEXT,               -- JSON array
    discussion_opportunities TEXT,      -- JSON array
    relevance_score REAL DEFAULT 0.0,   -- 0.0 to 1.0
    analyzed_at     TIMESTAMP,

    -- Processing status
    status          TEXT DEFAULT 'new', -- new, analyzed, posted, skipped, error
    error_message   TEXT,

    FOREIGN KEY (source_id) REFERENCES sources(id)
);

-- ------------------------------------------------------------
-- GENERATED POSTS TABLE: AI-generated Reddit posts
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generated_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL,
    subreddit       TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    post_type       TEXT DEFAULT 'text',  -- text, link, hybrid
    flair           TEXT,
    status          TEXT DEFAULT 'draft', -- draft, approved, rejected, posted, failed
    approval_mode   TEXT,                 -- A, B, C
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     TIMESTAMP,
    reviewer_notes  TEXT,
    generation_model TEXT,

    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- ------------------------------------------------------------
-- POSTED TO REDDIT TABLE: All published posts
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reddit_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_post_id INTEGER NOT NULL,
    article_id      INTEGER NOT NULL,
    reddit_post_id  TEXT UNIQUE,         -- Reddit's post ID (t3_xxxxx)
    reddit_url      TEXT,
    subreddit       TEXT NOT NULL,
    reddit_account  TEXT,
    title           TEXT NOT NULL,
    body            TEXT,
    post_type       TEXT,
    posted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'posted', -- posted, deleted, removed, failed

    -- Performance metrics (updated by tracker)
    upvotes         INTEGER DEFAULT 0,
    downvotes       INTEGER DEFAULT 0,
    score           INTEGER DEFAULT 0,
    upvote_ratio    REAL DEFAULT 0.0,
    num_comments    INTEGER DEFAULT 0,
    engagement_rate REAL DEFAULT 0.0,
    last_tracked    TIMESTAMP,

    FOREIGN KEY (generated_post_id) REFERENCES generated_posts(id),
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- ------------------------------------------------------------
-- DUPLICATE CHECK TABLE: Fast URL lookup for duplicates
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_urls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    url_hash        TEXT NOT NULL,
    content_hash    TEXT,               -- Hash of article content
    processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processing_type TEXT               -- article, reddit_post
);

-- ------------------------------------------------------------
-- SYSTEM LOGS TABLE: Activity and error logs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    level           TEXT NOT NULL,      -- INFO, WARNING, ERROR, DEBUG
    module          TEXT,
    message         TEXT NOT NULL,
    details         TEXT,               -- JSON extra details
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- SUBREDDIT STATS TABLE: Per-subreddit performance tracking
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subreddit_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subreddit       TEXT NOT NULL,
    date            DATE NOT NULL,
    posts_count     INTEGER DEFAULT 0,
    total_upvotes   INTEGER DEFAULT 0,
    total_comments  INTEGER DEFAULT 0,
    avg_score       REAL DEFAULT 0.0,
    best_post_id    TEXT,
    UNIQUE(subreddit, date)
);

-- ------------------------------------------------------------
-- TWITTER POSTS TABLE: Published tweets
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS twitter_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER,
    tweet_text      TEXT NOT NULL,
    tweet_type      TEXT DEFAULT 'single',  -- single, thread
    thread_tweets   TEXT,                   -- JSON array of tweet texts (for threads)
    tweet_id        TEXT UNIQUE,            -- Twitter's tweet ID
    tweet_url       TEXT,
    twitter_account TEXT,
    status          TEXT DEFAULT 'draft',   -- draft, approved, posted, failed, skipped
    approval_mode   TEXT DEFAULT 'B',
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at       TIMESTAMP,

    -- Performance metrics
    likes           INTEGER DEFAULT 0,
    retweets        INTEGER DEFAULT 0,
    replies         INTEGER DEFAULT 0,
    impressions     INTEGER DEFAULT 0,
    last_tracked    TIMESTAMP,

    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- ------------------------------------------------------------
-- QUORA DRAFTS TABLE: Generated Quora answers (ready to paste)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quora_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER,
    question        TEXT NOT NULL,          -- The Quora question this answers
    answer_text     TEXT NOT NULL,          -- Full formatted answer
    topics          TEXT,                   -- JSON array of Quora topics/spaces
    word_count      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'draft',   -- draft, exported, posted_manually, skipped
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exported_at     TIMESTAMP,
    notes           TEXT,

    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- ------------------------------------------------------------
-- INDEXES for fast lookups
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_at);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_generated_status ON generated_posts(status);
CREATE INDEX IF NOT EXISTS idx_reddit_posts_subreddit ON reddit_posts(subreddit);
CREATE INDEX IF NOT EXISTS idx_reddit_posts_posted ON reddit_posts(posted_at);
CREATE INDEX IF NOT EXISTS idx_processed_urls_hash ON processed_urls(url_hash);
CREATE INDEX IF NOT EXISTS idx_logs_level ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_created ON system_logs(created_at);
