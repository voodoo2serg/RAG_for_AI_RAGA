from raga_community.extract import extract_knowledge, rank_text

def test_extract():
    result=extract_knowledge("TODO: write docs\nRule: verify facts\nDecision: use local mode\nSkill: publish report")
    assert all(len(result[k])==1 for k in ("ticket","rule","decision","skill"))

def test_rank():
    assert rank_text("context ticket","context ticket")==1.0
    assert rank_text("context ticket","irrelevant")==0.0
