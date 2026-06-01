"""
Reddit Post Generator Module
Uses AI + your custom writing instructions to generate
authentic, human-sounding Reddit discussion posts.
"""

import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Path to writing instructions
INSTRUCTIONS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'config', 'writing_instructions.md'
)


def load_writing_instructions() -> str:
    """Load the latest writing instructions from file. Always reads fresh."""
    try:
        with open(INSTRUCTIONS_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("writing_instructions.md not found, using defaults")
        return DEFAULT_INSTRUCTIONS


class PostGenerator:
    """Generates Reddit posts from analyzed articles using AI."""

    def __init__(self, config: Dict, analyzer=None):
        self.config = config
        self.analyzer = analyzer  # ContentAnalyzer instance for LLM calls

    def generate_post(
        self,
        article: Dict,
        analysis: Dict,
        subreddit_config: Dict,
        post_type: str = 'text'
    ) -> Optional[Dict]:
        """
        Generate a Reddit post for a specific subreddit.
        Returns dict with title and body, or None on failure.
        """
        # Always load fresh writing instructions
        writing_instructions = load_writing_instructions()

        subreddit = subreddit_config.get('name', 'unknown')
        subreddit_rules = subreddit_config.get('rules_summary', '')
        subreddit_instructions = subreddit_config.get('custom_instructions', '')

        # Build context from analysis
        key_insights = analysis.get('key_insights', [])
        discussion_ops = analysis.get('discussion_opportunities', [])
        best_angle = analysis.get('best_post_angle', '')
        summary = analysis.get('summary', '')

        logger.info(f"Generating post for r/{subreddit}: {article.get('title', '')[:50]}")

        prompt = f"""You are generating a Reddit post for r/{subreddit}. You must follow the writing instructions EXACTLY.

===== WRITING INSTRUCTIONS =====
{writing_instructions}
===== END INSTRUCTIONS =====

===== SUBREDDIT-SPECIFIC RULES =====
Subreddit: r/{subreddit}
Rules: {subreddit_rules}
Additional instructions: {subreddit_instructions}
===== END SUBREDDIT RULES =====

===== SOURCE ARTICLE CONTEXT =====
Article Title: {article.get('title', '')}
Article Summary: {summary}
Key Insights:
{chr(10).join(f"- {i}" for i in key_insights[:5])}
Discussion Opportunities:
{chr(10).join(f"- {d}" for d in discussion_ops[:3])}
Best Post Angle: {best_angle}
===== END CONTEXT =====

TASK: Write an ORIGINAL Reddit post for r/{subreddit} INSPIRED by the article above.

Requirements:
- DO NOT copy or quote from the article
- DO NOT mention the article or say "I read an article"
- Write as if you are a genuine community member sharing your own thoughts/observations
- Follow the writing instructions exactly for tone, structure, and length
- The post should spark genuine discussion
- Post type: {post_type}

Respond ONLY with a JSON object in this exact format:
{{
  "title": "Your Reddit post title here (50-100 characters, no clickbait)",
  "body": "Your Reddit post body here (100-300 words, conversational paragraphs, NO bullet points, NO headers)"
}}

Only the JSON. Nothing else."""

        if not self.analyzer:
            logger.error("No analyzer (LLM) configured")
            return None

        response_text = self.analyzer._call_llm(prompt)
        if not response_text:
            return None

        post_data = self._parse_post_json(response_text)
        if not post_data:
            return None

        # Validate the generated post
        if not self._validate_post(post_data):
            logger.warning("Generated post failed validation, retrying once...")
            # Retry once with stricter prompt
            return self._retry_generation(article, analysis, subreddit_config, writing_instructions)

        return {
            'title': post_data['title'],
            'body': post_data['body'],
            'subreddit': subreddit,
            'post_type': post_type,
            'article_id': article.get('id'),
            'generation_model': f"{self.config.get('ai', {}).get('provider', 'unknown')}:{self.config.get('ai', {}).get('model', 'unknown')}"
        }

    def generate_variations(
        self,
        article: Dict,
        analysis: Dict,
        subreddit_configs: List[Dict]
    ) -> List[Dict]:
        """Generate one post variation per target subreddit."""
        posts = []
        for sub_config in subreddit_configs:
            if not sub_config.get('enabled', True):
                continue

            # Check if this subreddit matches the article's category
            sub_categories = sub_config.get('categories', [])
            article_category = article.get('category', '')
            if sub_categories and article_category and article_category not in sub_categories:
                logger.debug(
                    f"Skipping r/{sub_config['name']}: "
                    f"category '{article_category}' not in {sub_categories}"
                )
                continue

            post = self.generate_post(
                article, analysis, sub_config,
                post_type=sub_config.get('post_type', 'text')
            )
            if post:
                post['flair'] = sub_config.get('flair', '')
                posts.append(post)

        return posts

    def _retry_generation(self, article, analysis, subreddit_config, writing_instructions) -> Optional[Dict]:
        """Retry post generation with a simpler prompt."""
        subreddit = subreddit_config.get('name', 'unknown')
        summary = analysis.get('summary', article.get('title', ''))

        simple_prompt = f"""Write a short, natural Reddit post for r/{subreddit}.

Topic inspiration: {summary}

Rules:
- Sound like a real person, not a bot
- 1-2 short paragraphs
- End with a question
- No bullet points, no headers
- Conversational tone

Respond ONLY with JSON:
{{"title": "natural title here", "body": "body text here"}}"""

        text = self.analyzer._call_llm(simple_prompt)
        return self._parse_post_json(text) if text else None

    def _parse_post_json(self, text: str) -> Optional[Dict]:
        """Parse JSON from generator response."""
        if not text:
            return None

        text = text.strip()

        # Remove markdown code blocks
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        # Find JSON object
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end+1]

        try:
            data = json.loads(text)
            if 'title' in data and 'body' in data:
                return data
            logger.warning("JSON missing title or body fields")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse post JSON: {e}")
            # Try to extract title and body with regex
            import re
            title_match = re.search(r'"title"\s*:\s*"([^"]+)"', text)
            body_match = re.search(r'"body"\s*:\s*"([^"]+)"', text, re.DOTALL)
            if title_match and body_match:
                return {
                    'title': title_match.group(1),
                    'body': body_match.group(1).replace('\\n', '\n')
                }
            return None

    def _validate_post(self, post: Dict) -> bool:
        """Validate that a generated post meets minimum quality standards."""
        title = post.get('title', '')
        body = post.get('body', '')

        # Title checks
        if len(title) < 10:
            logger.warning(f"Title too short: '{title}'")
            return False
        if len(title) > 300:
            logger.warning("Title too long")
            return False

        # Body checks
        if len(body) < 50:
            logger.warning("Body too short")
            return False

        # Check for signs of bad generation
        bad_phrases = [
            'as an ai', 'as a language model', 'i cannot', 'i apologize',
            'article mentions', 'according to the article', 'i just read',
            'check out this article', 'amazing article'
        ]
        body_lower = body.lower()
        title_lower = title.lower()
        for phrase in bad_phrases:
            if phrase in body_lower or phrase in title_lower:
                logger.warning(f"Bad phrase detected: '{phrase}'")
                return False

        return True


# Default writing instructions fallback
DEFAULT_INSTRUCTIONS = """
Write a natural, conversational Reddit post.
Sound like a genuine community member.
Keep it 100-250 words.
End with a question to spark discussion.
No bullet points, no headers.
Avoid marketing language and buzzwords.
"""
