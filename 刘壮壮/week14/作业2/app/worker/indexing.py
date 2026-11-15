"""Markdown chunking, embeddings, and idempotent Elasticsearch writes."""
import hashlib
import math
import re
import json
import unicodedata
from pathlib import Path

from app.config import get_settings


def split_chunks(markdown: str, target_chars: int = 1800, overlap_chars: int = 240) -> list[dict]:
    """Split on Markdown headings and paragraph boundaries, keeping a stable heading path."""
    chunks, heading_stack, buffer = [], [], []
    def flush():
        text = "\n\n".join(buffer).strip()
        if text:
            chunks.append({"content": text, "heading_path": " > ".join(heading_stack),
                           "heading": heading_stack[-1] if heading_stack else ""})
        buffer.clear()
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            level, title = len(match.group(1)), match.group(2)
            heading_stack[:] = heading_stack[:level - 1] + [title]
            continue
        if not line.strip():
            if buffer:
                flush()
            continue
        if sum(map(len, buffer)) + len(line) > target_chars:
            previous = buffer[-1:] if buffer else []
            flush()
            # Carry a short tail into the next chunk to preserve local context.
            if previous and len(previous[0]) <= overlap_chars:
                buffer.extend(previous)
        buffer.append(line)
    flush()
    return chunks


def tokenize(text: str) -> str:
    """Use jieba when installed, with a deterministic whitespace fallback for local setup."""
    try:
        import jieba
        return " ".join(x.strip().lower() for x in jieba.lcut(text) if x.strip())
    except ImportError:
        return " ".join(re.findall(r"[\w]+|[\u4e00-\u9fff]", text.lower()))


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if settings.adapter_mode == "real":
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        return model.encode(texts, normalize_embeddings=True).tolist()
    # Local placeholder: deterministic 512-dimensional vectors for exercising the write path.
    vectors = []
    for text in texts:
        values = [0.0] * 512
        tokens = re.findall(r"\w+|[\u4e00-\u9fff]", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % 512
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v*v for v in values)) or 1.0
        vectors.append([v/norm for v in values])
    return vectors


def ensure_index(client, index_name: str):
    physical = "rag_chunks_v2" if index_name == "rag_chunks" else index_name
    exists=client.indices.exists(index=physical)
    if not exists:
        client.indices.create(index=physical, settings={"number_of_shards":1,"number_of_replicas":0,
            "analysis":{"analyzer":{"terms_whitespace":{"type":"whitespace"}}}}, mappings={"dynamic":"strict","properties":{
                "document_id":{"type":"keyword"},"document_version_id":{"type":"keyword"},"chunk_index":{"type":"integer"},
                "content":{"type":"text","index":False},"content_terms":{"type":"text","analyzer":"terms_whitespace"},
                "heading":{"type":"text","index":False},"heading_terms":{"type":"text","analyzer":"terms_whitespace"},
                "heading_path":{"type":"keyword"},"page_start":{"type":"integer"},"page_end":{"type":"integer"},
                "locations":{"type":"nested","properties":{"page":{"type":"integer"},"page_width":{"type":"float"},"page_height":{"type":"float"},"bbox":{"type":"float"},"text":{"type":"text","index":False}}},
                "content_type":{"type":"keyword"},"image_asset_ids":{"type":"keyword"},
                "embedding":{"type":"dense_vector","element_type":"float","dims":512,"index":True,"similarity":"cosine","index_options":{"type":"int8_hnsw","m":16,"ef_construction":100}}}})
    if index_name != "rag_chunks": return
    aliases=client.indices.get_alias(name=index_name,ignore_unavailable=True)
    old_indices=list(aliases.keys()) if aliases else []
    if physical in old_indices: return
    # Preserve existing searchable chunks while the new index receives coordinates on future parses.
    for old in old_indices:
        client.reindex(body={"source":{"index":old},"dest":{"index":physical}},wait_for_completion=True,refresh=True,conflicts="proceed")
    actions=[{"remove":{"index":old,"alias":index_name}} for old in old_indices]
    actions.append({"add":{"index":physical,"alias":index_name,"is_write_index":True}})
    client.indices.update_aliases(actions=actions)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def _layout_paragraphs(path: str | None) -> list[dict]:
    if not path or not Path(path).is_file(): return []
    data=json.loads(Path(path).read_text(encoding="utf-8",errors="replace"))
    out=[]
    for fallback,page in enumerate(data.get("pdf_info",[]),1):
        page_no=int(page.get("page_idx",fallback-1))+1
        size=page.get("page_size") or [0,0]
        for block in page.get("para_blocks",[]):
            text="".join(span.get("content","") for line in block.get("lines",[]) for span in line.get("spans",[])).strip()
            bbox=block.get("bbox")
            if text and isinstance(bbox,list) and len(bbox)==4 and size[0] and size[1]:
                out.append({"page":page_no,"page_width":size[0],"page_height":size[1],"bbox":bbox,"text":text})
    return out


def index_markdown(document_id: int, version_id: int, markdown: str, layout_path: str | None = None) -> int:
    from elasticsearch.helpers import bulk
    from app.clients.elasticsearch import create_client
    settings = get_settings()
    chunks = split_chunks(markdown)
    paragraphs=_layout_paragraphs(layout_path)
    for chunk in chunks:
        content=_norm(chunk["content"])
        raw_matched=[p for p in paragraphs if _norm(p["text"]) and _norm(p["text"]) in content]
        matched=[p for p in raw_matched if not any(q["page"]!=p["page"] and len(_norm(q["text"]))>len(_norm(p["text"])) and _norm(p["text"]) in _norm(q["text"]) for q in raw_matched)] or raw_matched
        chunk["locations"]=matched
        chunk["page_start"]=min((p["page"] for p in matched),default=None)
        chunk["page_end"]=max((p["page"] for p in matched),default=None)
    if not chunks:
        raise ValueError("No text chunks were produced from MinerU Markdown")
    vectors = embed([c["content"] for c in chunks])
    if any(len(v) != 512 for v in vectors):
        raise ValueError("Embedding model must produce 512-dimensional vectors")
    client = create_client()
    ensure_index(client, settings.elasticsearch_index)
    from app.db import connect
    with connect() as db:
        assets=[dict(r) for r in db.execute("SELECT id,filename,page_number FROM document_assets WHERE document_version_id=?",(version_id,))]
    actions = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        heading = chunk["heading"]
        # MinerU Markdown image links provide an explicit association when available.
        referenced={PathPart.lower() for PathPart in re.findall(r"!\[[^\]]*\]\(([^)]+)\)",chunk["content"])}
        image_ids=[str(a["id"]) for a in assets if a["filename"].lower() in referenced or any(a["filename"].lower() in ref for ref in referenced)]
        actions.append({"_op_type":"index","_index":settings.elasticsearch_index,
            "_id":f"{version_id}_chunk_{i:06d}","_source":{
                "document_id":str(document_id),"document_version_id":str(version_id),"chunk_index":i,
                "content":chunk["content"],"content_terms":tokenize(chunk["content"]),"heading":heading,
                "heading_terms":tokenize(heading),"heading_path":chunk["heading_path"],"page_start":chunk["page_start"],"page_end":chunk["page_end"],"locations":chunk["locations"],
                "content_type":"mixed" if image_ids else "text","image_asset_ids":image_ids,"embedding":vector}})
    success, errors = bulk(client, actions, raise_on_error=False, refresh=False)
    if errors:
        raise RuntimeError(f"Elasticsearch bulk indexing failed for {len(errors)} chunks")
    client.indices.refresh(index=settings.elasticsearch_index)
    return success
