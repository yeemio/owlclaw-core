"""Static site generator for OwlHub index data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape


@dataclass(frozen=True)
class SkillPage:
    """Rendered page info for one skill version."""

    url_path: str
    file_path: Path


class SiteGenerator:
    """Generate static HTML pages and metadata artifacts from index payload."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        default_templates = Path(__file__).resolve().parent / "templates"
        self.templates_dir = templates_dir or default_templates
        self._env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate(
        self,
        *,
        index_data: dict[str, Any],
        output_dir: Path,
        base_url: str = "https://owlhub.local",
        page_size: int = 20,
    ) -> None:
        """Generate index/detail/search pages, rss feed, sitemap and search metadata."""
        output_dir.mkdir(parents=True, exist_ok=True)
        skills = index_data.get("skills", [])
        generated_at = str(index_data.get("generated_at", ""))
        pages = self._render_pages(skills=skills, generated_at=generated_at, output_dir=output_dir, page_size=page_size)

        search_index_path = output_dir / "search-index.json"
        search_index = index_data.get("search_index", [])
        search_index_path.write_text(json.dumps(search_index, ensure_ascii=False, indent=2), encoding="utf-8")

        rss_path = output_dir / "rss.xml"
        rss_path.write_text(self._build_rss(skills=skills, base_url=base_url), encoding="utf-8")

        sitemap_path = output_dir / "sitemap.xml"
        sitemap_path.write_text(self._build_sitemap(pages=pages, base_url=base_url), encoding="utf-8")

    def _render_pages(
        self,
        *,
        skills: list[dict[str, Any]],
        generated_at: str,
        output_dir: Path,
        page_size: int,
    ) -> list[SkillPage]:
        pages: list[SkillPage] = []
        normalized_skills = [self._normalize_skill(item) for item in skills]
        tag_cloud = self._build_tag_cloud(normalized_skills)

        index_template = self._env.get_template("index.html")
        paged = self._paginate(normalized_skills, page_size=page_size)
        pages_dir = output_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for page_no, page_skills in enumerate(paged, start=1):
            file_path = output_dir / "index.html" if page_no == 1 else pages_dir / f"page-{page_no}.html"
            prev_url = "/index.html" if page_no == 2 else f"/pages/page-{page_no - 1}.html"
            next_url = f"/pages/page-{page_no + 1}.html"
            file_path.write_text(
                index_template.render(
                    skills=page_skills,
                    generated_at=generated_at,
                    tag_cloud=tag_cloud,
                    page_no=page_no,
                    total_pages=len(paged),
                    prev_url=prev_url if page_no > 1 else "",
                    next_url=next_url if page_no < len(paged) else "",
                ),
                encoding="utf-8",
            )
            url_path = "/index.html" if page_no == 1 else f"/pages/page-{page_no}.html"
            pages.append(SkillPage(url_path=url_path, file_path=file_path))

        search_template = self._env.get_template("search.html")
        search_output = output_dir / "search.html"
        search_output.write_text(
            search_template.render(skills=normalized_skills, generated_at=generated_at, tag_cloud=tag_cloud),
            encoding="utf-8",
        )
        pages.append(SkillPage(url_path="/search.html", file_path=search_output))

        dashboard_template = self._env.get_template("dashboard.html")
        dashboard_output = output_dir / "dashboard.html"
        top_skills = sorted(
            normalized_skills,
            key=lambda item: int(item.get("statistics", {}).get("total_downloads", 0)),
            reverse=True,
        )[:10]
        recent_skills = sorted(
            normalized_skills,
            key=lambda item: str(item.get("statistics", {}).get("last_updated", "")),
            reverse=True,
        )[:10]
        dashboard_output.write_text(
            dashboard_template.render(
                generated_at=generated_at,
                top_skills=top_skills,
                recent_skills=recent_skills,
                total_skills=len(normalized_skills),
            ),
            encoding="utf-8",
        )
        pages.append(SkillPage(url_path="/dashboard.html", file_path=dashboard_output))

        detail_template = self._env.get_template("skill_detail.html")
        skills_dir = output_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for item in normalized_skills:
            slug = _safe_file_stem(f"{item['publisher']}-{item['name']}-{item['version']}")
            file_name = f"{slug}.html"
            detail_path = skills_dir / file_name
            detail_path.write_text(detail_template.render(skill=item, generated_at=generated_at), encoding="utf-8")
            pages.append(SkillPage(url_path=f"/skills/{file_name}", file_path=detail_path))

        tag_template = self._env.get_template("tag.html")
        tags_dir = output_dir / "tags"
        tags_dir.mkdir(parents=True, exist_ok=True)
        for tag_name in sorted(tag_cloud.keys()):
            tag_skills = [item for item in normalized_skills if tag_name in item["tags"]]
            slug = _slugify(tag_name)
            tag_path = tags_dir / f"{slug}.html"
            tag_path.write_text(
                tag_template.render(generated_at=generated_at, tag=tag_name, skills=tag_skills),
                encoding="utf-8",
            )
            pages.append(SkillPage(url_path=f"/tags/{slug}.html", file_path=tag_path))

        return pages

    @staticmethod
    def _paginate(skills: list[dict[str, Any]], *, page_size: int) -> list[list[dict[str, Any]]]:
        if page_size <= 0:
            page_size = 20
        if not skills:
            return [[]]
        return [skills[index : index + page_size] for index in range(0, len(skills), page_size)]

    @staticmethod
    def _build_tag_cloud(skills: list[dict[str, Any]]) -> dict[str, int]:
        cloud: dict[str, int] = {}
        for item in skills:
            for tag in item.get("tags", []):
                if isinstance(tag, str) and tag.strip():
                    cloud[tag] = cloud.get(tag, 0) + 1
        return dict(sorted(cloud.items(), key=lambda kv: (-kv[1], kv[0])))

    @staticmethod
    def _normalize_skill(item: dict[str, Any]) -> dict[str, Any]:
        manifest = item.get("manifest", {})
        return {
            "name": str(manifest.get("name", "")),
            "publisher": str(manifest.get("publisher", "")),
            "version": str(manifest.get("version", "")),
            "description": str(manifest.get("description", "")),
            "tags": [tag for tag in manifest.get("tags", []) if isinstance(tag, str)],
            "download_url": str(item.get("download_url", "")),
            "statistics": item.get("statistics", {}),
        }

    @staticmethod
    def _build_rss(*, skills: list[dict[str, Any]], base_url: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        items: list[str] = []
        for item in skills:
            manifest = item.get("manifest", {})
            name = escape(str(manifest.get("name", "")))
            publisher = escape(str(manifest.get("publisher", "")))
            version = escape(str(manifest.get("version", "")))
            description = escape(str(manifest.get("description", "")))
            slug = f"{publisher}-{name}-{version}".replace("/", "-")
            link = f"{base_url.rstrip('/')}/skills/{slug}.html"
            items.append(
                "<item>"
                f"<title>{name} {version}</title>"
                f"<link>{link}</link>"
                f"<description>{description}</description>"
                f"<pubDate>{escape(str(item.get('published_at', now)))}</pubDate>"
                "</item>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<rss version=\"2.0\"><channel>"
            "<title>OwlHub Updates</title>"
            f"<link>{base_url.rstrip('/')}</link>"
            "<description>Latest skill updates from OwlHub</description>"
            f"<lastBuildDate>{now}</lastBuildDate>"
            f"{''.join(items)}"
            "</channel></rss>"
        )

    @staticmethod
    def _build_sitemap(*, pages: list[SkillPage], base_url: str) -> str:
        now = datetime.now(timezone.utc).date().isoformat()
        urls = [
            "<url>"
            f"<loc>{escape(base_url.rstrip('/') + page.url_path)}</loc>"
            f"<lastmod>{now}</lastmod>"
            "</url>"
            for page in pages
        ]
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
            f"{''.join(urls)}"
            "</urlset>"
        )


def _slugify(value: str) -> str:
    return "-".join(value.strip().lower().split())


_SAFE_FILE_STEM_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_file_stem(value: str) -> str:
    cleaned = _SAFE_FILE_STEM_PATTERN.sub("-", value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "skill"
