"""Hybrid retrieval restricted to SQLite's currently active document versions."""
from app.clients.elasticsearch import create_client
from app.db import connect
from app.worker.indexing import embed, tokenize


def search(query: str, limit: int = 5, document_uids: list[str] | None = None) -> list[dict]:
    with connect() as db:
        # A reparse may be running while the previous completed version remains current and searchable.
        sql="""SELECT d.current_version_id FROM documents d
            JOIN document_versions v ON v.id=d.current_version_id
            WHERE d.status <> 'deleting' AND v.status='completed' AND d.current_version_id IS NOT NULL"""
        params=[]
        if document_uids:
            sql += " AND d.document_uid IN ("+','.join('?' for _ in document_uids)+")"
            params.extend(document_uids)
        versions = [str(r[0]) for r in db.execute(sql,params)]
    if not versions:
        return []
    es = create_client()
    query_terms = tokenize(query)
    filt = {"terms": {"document_version_id": versions}}
    lexical = es.search(index="rag_chunks", size=20, query={"bool":{"filter":[filt],"should":[
        {"match":{"heading_terms":{"query":query_terms,"boost":2}}},
        {"match":{"content_terms":{"query":query_terms}}}],"minimum_should_match":1}},
        source=["document_id","document_version_id","chunk_index","content","heading","page_start","page_end","locations","image_asset_ids"])
    vector = es.search(index="rag_chunks", size=20, knn={"field":"embedding","query_vector":embed([query])[0],
        "k":20,"num_candidates":100,"filter":filt},
        source=["document_id","document_version_id","chunk_index","content","heading","page_start","page_end","locations","image_asset_ids"])
    fused = {}
    for result in (lexical, vector):
        for rank, hit in enumerate(result["hits"]["hits"], start=1):
            key=hit["_id"]
            if key not in fused: fused[key]={"hit":hit,"rrf":0.0}
            fused[key]["rrf"] += 1/(60+rank)
    ranked=sorted(fused.values(),key=lambda x:x["rrf"],reverse=True)
    if not ranked: return []
    # Ignore very weak tail results while preserving a relative threshold per query.
    best=ranked[0]["rrf"]
    selected=[]; counts={}
    for item in ranked:
        if item["rrf"] < best * 0.35: continue
        source=item["hit"]["_source"]
        doc=source["document_id"]
        if counts.get(doc,0)>=2: continue
        selected.append({**source,"chunk_id":item["hit"]["_id"],"score":item["rrf"]})
        counts[doc]=counts.get(doc,0)+1
        if len(selected)>=limit: break
    # Attach display metadata from SQLite; file paths remain private.
    with connect() as db:
        for item in selected:
            doc=db.execute("SELECT document_uid,filename FROM documents WHERE id=?",(int(item["document_id"]),)).fetchone()
            item["document_uid"]=doc["document_uid"] if doc else None
            item["document_name"]=doc["filename"] if doc else "Deleted document"
    return selected
