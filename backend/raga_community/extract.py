"""Portable, deterministic text extraction adapted from the original RAGA project."""
import re

PATTERNS = {
    "ticket": ("todo", "need to", "must do", "напомни", "нужно", "сделать", "проверь", "- [ ]"),
    "rule": ("rule", "must", "never", "always", "правило", "нельзя", "обязательно", "всегда"),
    "decision": ("decision", "decided", "conclusion", "решение", "решили", "фиксируем"),
    "skill": ("skill", "workflow", "procedure", "навык", "процедура", "инструкция"),
}

def extract_knowledge(text: str) -> dict[str, list[str]]:
    results = {key: [] for key in PATTERNS}
    seen = {key: set() for key in PATTERNS}
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        low = value.casefold()
        for kind, words in PATTERNS.items():
            if any(term in low for term in words) and low not in seen[kind]:
                results[kind].append(value[:1000])
                seen[kind].add(low)
    return results

def rank_text(query: str, text: str) -> float:
    tokens = set(re.findall(r"\w+", query.casefold(), re.UNICODE))
    if not tokens:
        return 0.0
    terms = set(re.findall(r"\w+", text.casefold(), re.UNICODE))
    return round(len(terms & tokens) / len(tokens), 4)
