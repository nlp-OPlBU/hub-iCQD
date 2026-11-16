import re
from rank_bm25 import BM25Okapi
from .storage import db

def tokens(text):
    text = text.lower()
    english = re.findall(r'[a-z0-9_]+', text)
    han = re.findall(r'[\u4e00-\u9fff]+', text)
    return english + [part[i:i + 2] for part in han for i in range(max(1, len(part) - 1))]

def retrieve(query, doc_ids=None, limit=6):
    with db() as c:
        sql = "SELECT chunks.*,documents.name FROM chunks JOIN documents ON documents.id=chunks.doc_id WHERE documents.status='completed'"
        args = []
        if doc_ids:
            sql += ' AND doc_id IN (' + ','.join('?' for _ in doc_ids) + ')'
            args = doc_ids
        rows = [dict(r) for r in c.execute(sql, args)]
    if not rows:
        return []
    corpus = [tokens(x['text']) or ['empty'] for x in rows]
    bm25 = BM25Okapi(corpus, epsilon=0.25)
    query_tokens = set(tokens(query))
    scores = bm25.get_scores(list(query_tokens))
    # BM25 can be zero/negative for a tiny corpus. A small overlap term still ranks real matches.
    ranked = sorted(zip(rows, corpus, scores),
                    key=lambda x: (sum(t in query_tokens for t in set(x[1])) + float(x[2])), reverse=True)
    selected = [dict(row, score=round(float(score), 3)) for row, terms, score in ranked
                if query_tokens.intersection(terms)][:limit]
    # Pair text evidence with nearby figure blocks for a genuinely multimodal context.
    if selected:
        for candidate in rows:
            if candidate['image_url'] and any(x['doc_id'] == candidate['doc_id'] and abs(x['page'] - candidate['page']) <= 1 for x in selected):
                if candidate['id'] not in {x['id'] for x in selected}:
                    selected.append(dict(candidate, score=0.0))
                if sum(bool(x['image_url']) for x in selected) >= 3:
                    break
    return selected[:9]
