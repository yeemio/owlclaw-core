"""Template searcher — relevance-based search and filtering."""

from __future__ import annotations

import logging
from contextlib import suppress

from owlclaw.templates.skills.models import SearchResult, TemplateCategory, TemplateMetadata
from owlclaw.templates.skills.registry import TemplateRegistry

logger = logging.getLogger(__name__)


class TemplateSearcher:
    """Searches templates by query with relevance scoring and filtering."""

    def __init__(self, registry: TemplateRegistry) -> None:
        """Initialize searcher with a template registry."""
        self.registry = registry

    def search(
        self,
        query: str,
        category: TemplateCategory | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search templates by query, optionally filtered by category and tags.

        Args:
            query: Search keyword.
            category: Optional category filter.
            tags: Optional tag filter (templates must match any of these).
            limit: Maximum number of results.

        Returns:
            Search results sorted by relevance (descending).
        """
        if limit <= 0:
            return []
        logger.debug("Searching templates: query=%r, category=%s, tags=%s", query, category, tags)
        candidates = self.registry.list_templates(category=category, tags=tags)
        results: list[SearchResult] = []
        for template in candidates:
            score, reason = self._calculate_relevance(query, template)
            if score > 0:
                results.append(
                    SearchResult(
                        template=template,
                        score=score,
                        match_reason=reason,
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _calculate_relevance(self, query: str, template: TemplateMetadata) -> tuple[float, str]:
        """Calculate relevance score for a template against a query.

        Returns:
            (score 0–1, match reason string)
        """
        query_lower = query.lower().strip()
        if not query_lower:
            return 0.0, "empty query"

        score = 0.0
        reasons: list[str] = []

        if query_lower == template.name.lower():
            score += 1.0
            reasons.append("exact name match")
        elif query_lower in template.name.lower():
            score += 0.8
            reasons.append("name contains query")

        if query_lower in template.description.lower():
            score += 0.5
            reasons.append("description contains query")

        for tag in template.tags:
            if query_lower in tag.lower():
                score += 0.6
                reasons.append(f"tag match: {tag}")
                break

        if query_lower in template.category.value.lower():
            score += 0.3
            reasons.append("category match")

        reason = ", ".join(reasons) if reasons else "no match"
        return min(score, 1.0), reason

    def recommend(
        self,
        context: dict | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Recommend templates based on context (optional).

        Simple implementation: returns top templates by category when
        use_case is provided, otherwise returns templates from all categories.

        Args:
            context: Optional dict with use_case, existing_skills, tech_stack.
            limit: Maximum results.

        Returns:
            Recommended templates.
        """
        if limit <= 0:
            return []
        context = context or {}
        use_case = context.get("use_case") or ""
        category_str = (context.get("category") or "").lower()

        category: TemplateCategory | None = None
        if category_str:
            with suppress(ValueError):
                category = TemplateCategory(category_str)

        if use_case:
            return self.search(use_case, category=category, limit=limit)

        templates = self.registry.list_templates(category=category)
        return [
            SearchResult(template=t, score=0.5, match_reason="recommended")
            for t in templates[:limit]
        ]
