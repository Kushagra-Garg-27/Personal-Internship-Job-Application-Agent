"""Local embedding-based relevance scoring stage (Phase 4 Stage 2/3).

Computes cosine similarity between candidate resume/skills and job description
using sentence-transformers (`all-MiniLM-L6-v2`).
Zero cloud or API calls — runs entirely locally on CPU.
Produces explainable breakdown: top matching skills and top matching sentences.
"""

from __future__ import annotations

import re
from typing import Any

from core.funnel.base import FunnelStage, StageVerdict

# Lazy-loaded singleton model instance
_MODEL_CACHE: dict[str, Any] = {}


def get_model(model_name: str = "all-MiniLM-L6-v2") -> Any:
    """Retrieve or lazily instantiate a SentenceTransformer model."""
    if model_name not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _MODEL_CACHE[model_name]


def split_sentences(text: str) -> list[str]:
    """Split text into sentences cleanly."""
    if not text:
        return []
    # Split on periods/newlines followed by space or end, preserve meaningful clauses
    raw_splits = re.split(r"(?<=[.!?\n])\s+", text.strip())
    sentences = [s.strip() for s in raw_splits if len(s.strip()) >= 15]
    return sentences if sentences else [text.strip()[:200]]


class RelevanceScorer:
    """Encapsulates embedding generation, cosine similarity, and explanation generation."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name

    def _get_model(self) -> Any:
        return get_model(self.model_name)

    def score(
        self,
        resume_text: str | None = None,
        skills: list[str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Compute semantic similarity score and explainable breakdown.

        Returns:
            dict containing:
                score: float (0.0 to 1.0)
                explanation: dict with top_skills, top_sentences
                model_name: str
        """
        desc = (description or "").strip()
        if not desc:
            return {
                "score": 0.0,
                "explanation": {
                    "top_skills": [],
                    "top_sentences": [],
                    "note": "Empty job description",
                },
                "model_name": self.model_name,
            }

        skill_list = [s.strip() for s in (skills or []) if s and s.strip()]
        res_text = (resume_text or "").strip()

        if not skill_list and not res_text:
            return {
                "score": 0.0,
                "explanation": {
                    "top_skills": [],
                    "top_sentences": [],
                    "note": "No candidate resume or skills provided",
                },
                "model_name": self.model_name,
            }

        from sentence_transformers import util

        model = self._get_model()

        # Build candidate profile context text
        candidate_parts = []
        if skill_list:
            candidate_parts.append(f"Skills: {', '.join(skill_list)}")
        if res_text:
            candidate_parts.append(res_text)
        candidate_text = "\n\n".join(candidate_parts)

        # 1. Overall cosine similarity
        candidate_emb = model.encode(candidate_text, normalize_embeddings=True)
        desc_emb = model.encode(desc, normalize_embeddings=True)
        overall_sim = float(util.cos_sim(candidate_emb, desc_emb)[0][0])
        # Clamp to [0.0, 1.0] and round
        clamped_score = max(0.0, min(1.0, round(overall_sim, 4)))

        # 2. Per-skill contribution explanation
        top_skills = []
        if skill_list:
            skill_embs = model.encode(skill_list, normalize_embeddings=True)
            skill_sims = util.cos_sim(skill_embs, desc_emb)
            scored_skills = []
            for idx, skill in enumerate(skill_list):
                sim_val = float(skill_sims[idx][0])
                scored_skills.append({
                    "skill": skill,
                    "similarity": round(float(sim_val), 4),
                })
            scored_skills.sort(key=lambda item: item["similarity"], reverse=True)
            top_skills = scored_skills[:5]

        # 3. Top matching description sentences
        sentences = split_sentences(desc)
        top_sentences = []
        if sentences:
            sent_embs = model.encode(sentences, normalize_embeddings=True)
            sent_sims = util.cos_sim(candidate_emb, sent_embs)[0]
            scored_sentences = []
            for idx, sentence in enumerate(sentences):
                sim_val = float(sent_sims[idx])
                scored_sentences.append({
                    "sentence": sentence,
                    "similarity": round(float(sim_val), 4),
                })
            scored_sentences.sort(key=lambda item: item["similarity"], reverse=True)
            top_sentences = scored_sentences[:3]

        return {
            "score": clamped_score,
            "explanation": {
                "top_skills": top_skills,
                "top_sentences": top_sentences,
            },
            "model_name": self.model_name,
        }


class RelevanceStage(FunnelStage):
    """Stage 2/3: Semantic relevance scoring using local sentence-transformers."""

    name = "relevance"

    def __init__(self, scorer: RelevanceScorer | None = None) -> None:
        self._scorer = scorer or RelevanceScorer()

    @property
    def scorer(self) -> RelevanceScorer:
        return self._scorer

    def evaluate(
        self,
        opportunity: Any,
        profile: Any,
        resume_text: str | None = None,
    ) -> StageVerdict:
        """Score candidate relevance against opportunity description."""
        # Extract skills
        skills: list[str] = []
        if profile:
            skills = [
                getattr(s, "skill_name", str(s))
                for s in getattr(profile, "skills", [])
                if getattr(s, "skill_name", None) or isinstance(s, str)
            ]

        # Extract resume text if not passed directly
        text_to_use = resume_text
        if not text_to_use and profile:
            raw_resumes = getattr(profile, "resumes", [])
            resumes_list = raw_resumes if isinstance(raw_resumes, list) else ([raw_resumes] if raw_resumes else [])
            for r in resumes_list:
                if getattr(r, "is_active", False) and getattr(r, "parsed_text", None):
                    text_to_use = r.parsed_text
                    break
            if not text_to_use:
                # Fallback to any resume with parsed text
                for r in resumes_list:
                    if getattr(r, "parsed_text", None):
                        text_to_use = r.parsed_text
                        break

        description = getattr(opportunity, "description", None)
        result = self._scorer.score(
            resume_text=text_to_use,
            skills=skills,
            description=description,
        )

        return StageVerdict(
            stage_name=self.name,
            passed=True,
            reason=None,
            payload=result,
        )
