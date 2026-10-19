from __future__ import annotations


def build_narrative(
    score: float,
    grade: str,
    dimensions: list[dict],
    missing: list[str],
    confidence: str,
) -> dict:
    ranked = sorted(dimensions, key=lambda item: item["percentage"], reverse=True)
    strengths = []
    risks = []
    for dimension in ranked:
        strengths.extend(dimension["positives"][:2])
    for dimension in reversed(ranked):
        risks.extend(dimension["risks"][:2])

    best = ranked[0]
    weakest = ranked[-1]
    overview = (
        f"综合健康分 {score:.1f} 分，评级为“{grade}”。"
        f"表现最强的是{best['name']}（{best['score']}/{best['max_score']} 分），"
        f"最需要关注的是{weakest['name']}（{weakest['score']}/{weakest['max_score']} 分）。"
    )
    if missing:
        overview += f" 当前有 {len(missing)} 项指标缺失，数据可信度为{confidence}。"

    return {
        "overview": overview,
        "strengths": strengths[:5],
        "risks": risks[:5],
        "limitations": (
            "评分基于公开历史数据和固定规则，未纳入行业周期、公司治理、重大公告及未来预期；"
            "请结合原始财报和同行比较复核。"
        ),
    }
