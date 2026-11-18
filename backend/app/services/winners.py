"""Deterministic winner summaries from submitted scores and formal feedback.

Private notes are never read here. An optional OpenAI-compatible endpoint can
rewrite the narrative when WINNER_SUMMARY_API_URL is set. Any failure keeps
the rule-based summary.
"""

import json
import re
from collections import Counter

import httpx

from app.config import settings
from app.services.domain import DomainError

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "for", "to", "of", "with",
    "this", "that", "it", "its", "is", "are", "was", "were", "be", "been", "as", "at",
    "by", "from", "they", "their", "them", "i", "we", "our", "you", "your", "not",
    "so", "than", "then", "too", "very", "just", "about", "into", "over", "after",
    "before", "which", "what", "who", "how", "when", "where", "also", "can", "could",
    "would", "should", "have", "has", "had", "do", "did", "does", "project", "team",
}
_STRENGTHS = (
    "clear", "strong", "stronger", "useful", "polished", "working", "worked", "honest",
    "careful", "solid", "specific", "convincing", "concrete", "trusted", "real",
    "best", "respectful", "readable", "good",
)
_CONCERNS = (
    "thin", "slow", "trouble", "unclear", "generic", "hard-coded", "hard coded",
    "missing", "weak", "limited", "concern", "issue", "risk", "didn't", "did not",
    "far from", "less flashy", "not sure", "hard to",
)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " ").strip())
    return [part.strip() for part in parts if len(part.strip()) > 20]


def _kind(sentence: str) -> str:
    lower = sentence.lower()
    concern = any(word in lower for word in _CONCERNS)
    strength = any(word in lower for word in _STRENGTHS)
    if concern and not strength:
        return "concern"
    if strength and not concern:
        return "strength"
    return "note"


def _themes(chunks: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    for chunk in chunks:
        for word in re.findall(r"[a-zA-Z][a-zA-Z'-]{3,}", chunk.lower()):
            if word in _STOPWORDS:
                continue
            counts[word] += 1
    return [word for word, _count in counts.most_common(5)]


def _unique(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    chosen: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def summarize_feedback(formal_comments: list[dict], criterion_averages: dict, criteria: list[dict], overall: float | None) -> dict:
    strengths: list[str] = []
    concerns: list[str] = []
    notes: list[str] = []
    blobs: list[str] = []
    for comment in formal_comments:
        for field in ("formal_feedback", "alignment_explanation"):
            text = (comment.get(field) or "").strip()
            if not text:
                continue
            blobs.append(text)
            for sentence in _sentences(text):
                kind = _kind(sentence)
                if kind == "strength":
                    strengths.append(sentence)
                elif kind == "concern":
                    concerns.append(sentence)
                else:
                    notes.append(sentence)
    strengths = _unique(strengths, 4)
    concerns = _unique(concerns, 3)
    if not strengths and notes:
        strengths = _unique(notes, 3)
        notes = []
    themes = _themes(blobs)
    highlights = []
    if overall is not None:
        highlights.append(f"Overall average {overall:.2f}")
    ranked = []
    for criterion in criteria:
        value = criterion_averages.get(criterion["key"])
        if value is not None:
            ranked.append((value, criterion["name"]))
    if ranked:
        strongest = max(ranked)
        softest = min(ranked)
        highlights.append(f"Strongest criterion: {strongest[1]} ({strongest[0]:.2f})")
        if softest[1] != strongest[1]:
            highlights.append(f"Softest criterion: {softest[1]} ({softest[0]:.2f})")
    alignment = Counter(comment.get("alignment") or "unspecified" for comment in formal_comments)
    alignment_text = ", ".join(
        f"{alignment[key]} {key}" for key in ("strong", "partial", "weak") if alignment[key]
    ) or "No alignment recorded"
    paragraphs = []
    if highlights:
        paragraphs.append(" ".join(highlights) + ".")
    if strengths:
        paragraphs.append("Strengths: " + " ".join(strengths))
    if concerns:
        paragraphs.append("Concerns: " + " ".join(concerns))
    if themes:
        paragraphs.append("Themes judges returned to: " + ", ".join(themes) + ".")
    paragraphs.append(f"Track alignment: {alignment_text}.")
    if not blobs:
        paragraphs.append("No formal feedback was submitted.")
    return {
        "score_highlights": highlights,
        "strengths": strengths,
        "concerns": concerns,
        "themes": themes,
        "alignment": alignment_text,
        "text": " ".join(paragraphs),
        "source": "rules",
    }


def maybe_llm_narrative(packet: dict) -> str | None:
    url = (settings.winner_summary_api_url or "").strip()
    if not url:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.winner_summary_api_key:
        headers["Authorization"] = f"Bearer {settings.winner_summary_api_key}"
    body = {
        "model": settings.winner_summary_model or "gpt-4o-mini",
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize submitted hackathon judging feedback in 2 short paragraphs. "
                    "Use only the JSON you are given. Do not invent scores, quotes, or private notes."
                ),
            },
            {"role": "user", "content": json.dumps(packet)},
        ],
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=8.0)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    text = str(content).strip()
    return text or None


def build_winners(db, per_track: int) -> dict:
    if per_track < 1 or per_track > 10:
        raise DomainError("Choose between 1 and 10 winners per track.")
    from app.services.aggregation import build_results

    report = build_results(db)
    grouped: dict[int, list[dict]] = {}
    for project in report["projects"]:
        grouped.setdefault(project["track_id"], []).append(project)
    tracks = []
    for projects in grouped.values():
        ranked = [project for project in projects if project["rank"] is not None and project["rank"] <= per_track]
        ranked.sort(key=lambda project: (project["rank"], project["project_name"].lower()))
        winners = []
        for project in ranked:
            comments = []
            for evaluation in project["evaluations"]:
                if evaluation["status"] != "submitted":
                    continue
                comments.append(
                    {
                        "judge_id": evaluation["judge_id"],
                        "judge_name": evaluation.get("judge_name") or "",
                        "formal_feedback": evaluation["formal_feedback"],
                        "alignment": evaluation["alignment"],
                        "alignment_explanation": evaluation["alignment_explanation"],
                    }
                )
            summary = summarize_feedback(comments, project["criterion_averages"], report["criteria"], project["overall"])
            narrative = maybe_llm_narrative(
                {
                    "project": project["project_name"],
                    "track": project["track_name"],
                    "overall": project["overall"],
                    "criterion_averages": project["criterion_averages"],
                    "alignment_counts": project["alignment_counts"],
                    "formal_comments": comments,
                    "rule_summary": summary["text"],
                }
            )
            if narrative:
                summary = {**summary, "text": narrative, "source": "model"}
            winners.append(
                {
                    "project_id": project["project_id"],
                    "project_name": project["project_name"],
                    "description": project["description"],
                    "team_members": project["team_members"],
                    "rank": project["rank"],
                    "overall": project["overall"],
                    "criterion_averages": project["criterion_averages"],
                    "alignment_counts": project["alignment_counts"],
                    "discrepancy": project["discrepancy"],
                    "discrepancy_status": project["discrepancy_status"],
                    "summary": summary,
                    "formal_comments": comments,
                }
            )
        if not projects:
            continue
        tracks.append(
            {
                "track_id": projects[0]["track_id"],
                "track_name": projects[0]["track_name"],
                "winners": winners,
            }
        )
    tracks.sort(key=lambda item: item["track_name"].lower())
    return {"per_track": per_track, "criteria": report["criteria"], "tracks": tracks}


def winners_markdown(report: dict) -> str:
    lines = ["# Winner summaries", "", f"Top {report['per_track']} per track. Tied ranks are all included.", ""]
    criteria = report["criteria"]
    for track in report["tracks"]:
        lines.append(f"## {track['track_name']}")
        lines.append("")
        if not track["winners"]:
            lines.append("No submitted scores yet.")
            lines.append("")
            continue
        for winner in track["winners"]:
            lines.append(f"### {winner['rank']}. {winner['project_name']}")
            lines.append("")
            if winner["discrepancy"]:
                lines.append(f"Discrepancy: {winner['discrepancy_status']}.")
                lines.append("")
            overall = "—" if winner["overall"] is None else f"{winner['overall']:.2f}"
            lines.append(f"Overall average: {overall}")
            lines.append("")
            lines.append("Scores:")
            for criterion in criteria:
                value = winner["criterion_averages"].get(criterion["key"])
                shown = "—" if value is None else f"{value:.2f}"
                lines.append(f"- {criterion['name']}: {shown}")
            lines.append("")
            lines.append(winner["summary"]["text"])
            lines.append("")
            lines.append("What judges said:")
            if not winner["formal_comments"]:
                lines.append("- No formal feedback.")
            for comment in winner["formal_comments"]:
                alignment = comment["alignment"] or "no alignment"
                lines.append(f"- {comment['judge_name']} ({alignment}): {comment['formal_feedback'] or 'No formal feedback.'}")
                if comment["alignment_explanation"]:
                    lines.append(f"  Alignment: {comment['alignment_explanation']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def winners_csv(report: dict) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["track", "rank", "project", "overall", "discrepancy", "discrepancy_status", "summary"]
    header += [f"avg_{criterion['key']}" for criterion in report["criteria"]]
    header += ["judge", "alignment", "formal_feedback", "alignment_explanation"]
    writer.writerow(header)
    for track in report["tracks"]:
        for winner in track["winners"]:
            base = [
                track["track_name"],
                winner["rank"],
                winner["project_name"],
                "" if winner["overall"] is None else f"{winner['overall']:.2f}",
                "yes" if winner["discrepancy"] else "no",
                winner["discrepancy_status"] or "",
                winner["summary"]["text"],
            ]
            base += [
                ""
                if winner["criterion_averages"].get(criterion["key"]) is None
                else f"{winner['criterion_averages'][criterion['key']]:.2f}"
                for criterion in report["criteria"]
            ]
            comments = winner["formal_comments"] or [None]
            for comment in comments:
                row = list(base)
                if comment is None:
                    row += ["", "", "", ""]
                else:
                    row += [
                        comment["judge_name"],
                        comment["alignment"] or "",
                        comment["formal_feedback"],
                        comment["alignment_explanation"],
                    ]
                writer.writerow(row)
    return buffer.getvalue()
