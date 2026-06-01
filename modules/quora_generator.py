"""
Quora Answer Generator Module
Generates long-form, helpful Quora-style answers from the knowledge base.

Since Quora has NO public API, answers are saved as formatted drafts
that you can copy-paste directly into Quora. Each draft includes the
suggested question to answer and is ready to use immediately.

Drafts are saved to: quora_drafts/ folder as .md files for easy access.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'knowledge_base.db')
QUORA_INSTRUCTIONS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'config', 'quora_writing_instructions.md'
)
DRAFTS_EXPORT_DIR = os.path.join(os.path.dirname(__file__), '..', 'quora_drafts')


# ---------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_quora_draft(draft: Dict) -> Optional[int]:
    """Save a generated Quora answer draft."""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO quora_drafts (
                article_id, question, answer_text, topics, word_count, status
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            draft.get('article_id'),
            draft.get('question', ''),
            draft.get('answer_text', ''),
            json.dumps(draft.get('topics', [])),
            draft.get('word_count', 0),
            draft.get('status', 'draft')
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_quora_drafts(status: str = 'draft', limit: int = 50) -> List[Dict]:
    """Get Quora answer drafts."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT qd.*, a.title as article_title, a.url as article_url
            FROM quora_drafts qd
            LEFT JOIN articles a ON qd.article_id = a.id
            WHERE qd.status = ?
            ORDER BY qd.generated_at DESC LIMIT ?
        """, (status, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_quora_drafts(limit: int = 100) -> List[Dict]:
    """Get all Quora drafts regardless of status."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT qd.*, a.title as article_title
            FROM quora_drafts qd
            LEFT JOIN articles a ON qd.article_id = a.id
            ORDER BY qd.generated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_quora_draft_status(draft_id: int, status: str):
    """Update a Quora draft's status."""
    conn = _get_conn()
    try:
        extra = ""
        params = [status]
        if status == 'exported':
            extra = ", exported_at=CURRENT_TIMESTAMP"
        conn.execute(
            f"UPDATE quora_drafts SET status=?{extra} WHERE id=?",
            params + [draft_id]
        )
        conn.commit()
    finally:
        conn.close()


def get_quora_draft_by_id(draft_id: int) -> Optional[Dict]:
    """Get a single Quora draft."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM quora_drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def article_already_has_quora_draft(article_id: int) -> bool:
    """Check if this article already has a Quora draft."""
    conn = _get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM quora_drafts WHERE article_id=?", (article_id,)
        ).fetchone()[0]
        return count > 0
    finally:
        conn.close()


# ---------------------------------------------------------------
# QUORA ANSWER GENERATOR
# ---------------------------------------------------------------

def load_quora_instructions() -> str:
    """Load Quora writing instructions."""
    try:
        with open(QUORA_INSTRUCTIONS_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return DEFAULT_QUORA_INSTRUCTIONS


class QuoraGenerator:
    """Generates Quora-style answers from article analysis using AI."""

    def __init__(self, config: Dict, analyzer=None):
        self.config = config
        self.analyzer = analyzer
        self.quora_config = config.get('quora', {})

    def generate_answer(self, article: Dict, analysis: Dict) -> Optional[Dict]:
        """
        Generate a Quora question + answer pair from an article.
        Returns dict with question, answer_text, topics, word_count.
        """
        if not self.analyzer:
            logger.error("No analyzer configured for Quora generation")
            return None

        if article_already_has_quora_draft(article.get('id')):
            logger.debug(f"Article {article['id']} already has a Quora draft, skipping")
            return None

        instructions = load_quora_instructions()
        summary = analysis.get('summary', '')
        key_insights = analysis.get('key_insights', [])
        major_topics = analysis.get('major_topics', [])

        prompt = f"""You are writing a Quora answer. You are a knowledgeable person sharing genuine expertise.

WRITING INSTRUCTIONS:
{instructions}

ARTICLE CONTEXT:
Title: {article.get('title', '')}
Category: {article.get('category', '')}
Summary: {summary}
Key Insights:
{chr(10).join(f'- {i}' for i in key_insights[:5])}
Major Topics: {', '.join(major_topics[:5])}

TASK: Create a Quora question AND a full answer based on the insights above.

Requirements:
- First, invent a realistic Quora question someone would ask about this topic
- Write a genuine, helpful, expert answer (400-800 words)
- Structure with short paragraphs (no walls of text)
- Use Quora-style formatting: short intro, body with examples/insights, closing thought
- Sound like a knowledgeable person sharing real experience, not an AI
- Add 2-4 relevant Quora topic tags
- DO NOT mention the article or say "according to a recent article"
- NO bullet point lists — use natural prose paragraphs

Respond ONLY with valid JSON:
{{
  "question": "What is a realistic Quora question someone would search for?",
  "answer": "Your full Quora answer here (400-800 words, paragraphs, no bullet lists)",
  "topics": ["Topic1", "Topic2", "Topic3"]
}}"""

        response = self.analyzer._call_llm(prompt)
        if not response:
            return None

        data = self._parse_json(response)
        if not data:
            return None

        question = data.get('question', '').strip()
        answer = data.get('answer', '').strip()
        topics = data.get('topics', [])

        if not question or not answer or len(answer) < 100:
            logger.warning("Quora answer too short or missing question")
            return None

        word_count = len(answer.split())

        return {
            'article_id': article.get('id'),
            'question': question,
            'answer_text': answer,
            'topics': topics,
            'word_count': word_count,
            'status': 'draft'
        }

    def export_draft_to_file(self, draft: Dict) -> str:
        """
        Export a Quora draft as a markdown file for easy copy-pasting.
        Returns the file path.
        """
        os.makedirs(DRAFTS_EXPORT_DIR, exist_ok=True)

        # Safe filename from question
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9\s]', '', draft.get('question', 'answer'))
        safe_name = '_'.join(safe_name.split()[:8])[:60]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{timestamp}_{safe_name}.md"
        filepath = os.path.join(DRAFTS_EXPORT_DIR, filename)

        topics = draft.get('topics', [])
        if isinstance(topics, str):
            try:
                topics = json.loads(topics)
            except Exception:
                topics = []

        content = f"""# QUORA ANSWER DRAFT
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## QUESTION TO ANSWER:
> {draft.get('question', '')}

## SUGGESTED TOPICS/SPACES:
{', '.join(topics)}

---

## YOUR ANSWER:

{draft.get('answer_text', '')}

---

### HOW TO POST:
1. Go to https://www.quora.com
2. Search for the question above (or paste it in the search bar)
3. Click "Answer" on the question
4. Copy-paste the answer text above
5. Add the suggested topics/spaces
6. Click "Submit"

*Source article ID: {draft.get('article_id', 'N/A')}*
"""

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"Quora draft exported: {filepath}")
        return filepath

    def export_all_pending_drafts(self) -> int:
        """Export all pending Quora drafts to files."""
        drafts = get_quora_drafts(status='draft', limit=50)
        exported = 0
        for draft in drafts:
            try:
                self.export_draft_to_file(draft)
                update_quora_draft_status(draft['id'], 'exported')
                exported += 1
            except Exception as e:
                logger.error(f"Error exporting draft {draft['id']}: {e}")
        return exported

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
        except Exception as e:
            logger.warning(f"Could not parse Quora JSON: {e}")
            return None


DEFAULT_QUORA_INSTRUCTIONS = """
Write like a genuine expert sharing real knowledge.
Be helpful, clear, and authoritative.
Use short paragraphs. Start with a direct answer to the question.
Share examples or context to back up your points.
End with a takeaway or insight.
Never sound like an AI or mention AI.
"""
