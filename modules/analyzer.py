"""
AI Content Analyzer Module
Uses Ollama (free, local LLM) to analyze scraped articles:
- Extract key insights
- Identify major topics and trends
- Generate summaries
- Score relevance to Reddit communities
- Detect discussion opportunities
"""

import json
import logging
import os
import requests
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ContentAnalyzer:
    """Analyzes article content using a local or cloud LLM."""

    def __init__(self, config: Dict):
        self.config = config
        self.ai_config = config.get('ai', {})
        self.provider = self.ai_config.get('provider', 'ollama')
        self.model = self.ai_config.get('model', 'llama3.1')
        self.ollama_url = self.ai_config.get('ollama_url', 'http://localhost:11434')
        self.temperature = self.ai_config.get('temperature', 0.7)
        self.max_tokens = self.ai_config.get('max_tokens', 1500)

    def analyze_article(self, article: Dict) -> Optional[Dict]:
        """
        Run full AI analysis on an article.
        Returns analysis dict or None on failure.
        """
        title = article.get('title', '')
        content = article.get('content', '')
        category = article.get('category', '')

        if not content or len(content) < 100:
            logger.warning(f"Article too short to analyze: {title}")
            return None

        # Truncate very long articles to save tokens
        max_len = self.config.get('content', {}).get('max_article_length', 8000)
        content_for_analysis = content[:max_len]

        logger.info(f"Analyzing article: {title[:60]}")

        prompt = f"""You are an expert content analyst for Reddit communities. Analyze this article and provide structured insights.

ARTICLE TITLE: {title}
CATEGORY: {category}
CONTENT:
{content_for_analysis}

---

Analyze this article and respond ONLY with a valid JSON object in this exact format:

{{
  "summary": "A concise 2-3 sentence summary of the article's main point",
  "key_insights": [
    "First key insight or surprising fact from the article",
    "Second key insight",
    "Third key insight"
  ],
  "major_topics": [
    "Topic 1",
    "Topic 2",
    "Topic 3"
  ],
  "trends": [
    "Any trend mentioned or implied in this article"
  ],
  "discussion_opportunities": [
    "An interesting question this article raises for community discussion",
    "Another angle that could spark debate or engagement"
  ],
  "relevance_score": 0.8,
  "reddit_appeal": "Brief explanation of why Reddit communities would find this interesting and what kind of discussion it could spark",
  "best_post_angle": "The single best angle to approach this as a Reddit post — what framing would generate the most genuine discussion?"
}}

Only respond with the JSON. No explanation before or after."""

        analysis_text = self._call_llm(prompt)
        if not analysis_text:
            return None

        return self._parse_analysis_json(analysis_text)

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the configured LLM provider."""
        if self.provider == 'ollama':
            return self._call_ollama(prompt)
        elif self.provider == 'openai':
            return self._call_openai(prompt)
        elif self.provider == 'anthropic':
            return self._call_anthropic(prompt)
        else:
            logger.error(f"Unknown LLM provider: {self.provider}")
            return None

    def _call_ollama(self, prompt: str) -> Optional[str]:
        """Call local Ollama instance (completely free)."""
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": self.max_tokens,
                    }
                },
                timeout=120  # Ollama can take time for large models
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get('response', '')
        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama. Make sure Ollama is running.\n"
                "  → Install: https://ollama.ai\n"
                "  → Start: run 'ollama serve' in terminal\n"
                f"  → Pull model: run 'ollama pull {self.model}'"
            )
            return None
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return None

    def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API (requires API key)."""
        try:
            import openai
            client = openai.OpenAI(api_key=self.ai_config.get('openai_api_key'))
            response = client.chat.completions.create(
                model=self.model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content
        except ImportError:
            logger.error("openai package not installed. Run: pip install openai")
            return None
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return None

    def _call_anthropic(self, prompt: str) -> Optional[str]:
        """Call Anthropic Claude API (requires API key)."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.ai_config.get('anthropic_api_key'))
            message = client.messages.create(
                model=self.model or "claude-haiku-4-5-20251001",
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            return message.content[0].text
        except ImportError:
            logger.error("anthropic package not installed. Run: pip install anthropic")
            return None
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return None

    def _parse_analysis_json(self, text: str) -> Optional[Dict]:
        """Parse JSON from LLM response, handling common formatting issues."""
        if not text:
            return None

        # Try to extract JSON from the response
        text = text.strip()

        # Remove markdown code blocks if present
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        # Find JSON object in text
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end+1]

        try:
            data = json.loads(text)
            # Validate required fields
            required = ['summary', 'key_insights', 'major_topics']
            for field in required:
                if field not in data:
                    data[field] = [] if field != 'summary' else ''

            # Ensure lists are actually lists
            for list_field in ['key_insights', 'major_topics', 'trends', 'discussion_opportunities']:
                if list_field in data and not isinstance(data[list_field], list):
                    data[list_field] = [str(data[list_field])]
                elif list_field not in data:
                    data[list_field] = []

            # Ensure relevance_score is a float between 0 and 1
            score = data.get('relevance_score', 0.5)
            try:
                data['relevance_score'] = max(0.0, min(1.0, float(score)))
            except (ValueError, TypeError):
                data['relevance_score'] = 0.5

            return data

        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse JSON from LLM response: {e}")
            logger.debug(f"Raw response: {text[:500]}")
            # Return a basic structure so we don't fail completely
            return {
                'summary': text[:500] if len(text) < 500 else '',
                'key_insights': [],
                'major_topics': [],
                'trends': [],
                'discussion_opportunities': [],
                'relevance_score': 0.3,
                'reddit_appeal': '',
                'best_post_angle': ''
            }

    def check_ollama_status(self) -> Dict:
        """Check if Ollama is running and the model is available."""
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m['name'] for m in resp.json().get('models', [])]
                model_available = any(self.model in m for m in models)
                return {
                    'running': True,
                    'models': models,
                    'requested_model': self.model,
                    'model_available': model_available
                }
        except requests.ConnectionError:
            pass
        except Exception:
            pass
        return {'running': False, 'models': [], 'model_available': False}

    def pull_ollama_model(self) -> bool:
        """Pull the configured Ollama model if not already downloaded."""
        logger.info(f"Pulling Ollama model: {self.model} (this may take several minutes...)")
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/pull",
                json={"name": self.model, "stream": False},
                timeout=600  # 10 minutes for large models
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to pull model: {e}")
            return False
