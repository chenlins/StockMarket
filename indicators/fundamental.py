def score_value_metrics(
    pe: float,
    pb: float,
    roe: float,
    pe_max: float = 25,
    pb_max: float = 3,
    roe_min: float = 10,
) -> tuple[float, dict]:
    """
    价值投资打分 (0~100)。
    PE/PB 越低越好，ROE 越高越好。
    """
    details = {"pe": pe, "pb": pb, "roe": roe}
    score = 0.0

    if pe > 0:
        pe_score = max(0, (pe_max - pe) / pe_max * 40)
        score += pe_score
        details["pe_score"] = round(pe_score, 1)

    if pb > 0:
        pb_score = max(0, (pb_max - pb) / pb_max * 30)
        score += pb_score
        details["pb_score"] = round(pb_score, 1)

    if roe > 0:
        roe_score = min(30, (roe / roe_min) * 30) if roe >= roe_min else roe / roe_min * 15
        score += roe_score
        details["roe_score"] = round(roe_score, 1)

    return round(score, 1), details
