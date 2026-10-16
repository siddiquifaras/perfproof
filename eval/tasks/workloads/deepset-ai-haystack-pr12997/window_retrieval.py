"""Run SentenceWindowRetriever over an in-memory store repeatedly.

The store holds 50 source documents of 200 splits each. Each run
retrieves the windows around 10 hits, which the reference change turns
from 10 store queries into one. Prints the number of context documents
returned so both trees can be compared for equal output.
"""

import sys

from haystack import Document
from haystack.components.retrievers import SentenceWindowRetriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

store = InMemoryDocumentStore()
docs = [
    Document(
        content=f"source {s} sentence {i}. ",
        meta={"source_id": f"s{s}", "split_id": i, "split_idx_start": i * 20},
    )
    for s in range(50)
    for i in range(200)
]
store.write_documents(docs)
retriever = SentenceWindowRetriever(document_store=store, window_size=3)
hits = [docs[(k * 997) % len(docs)] for k in range(10)]
total = 0
for _ in range(20):
    out = retriever.run(retrieved_documents=hits)
    total += len(out["context_documents"]) + len(out["context_windows"])
sys.stdout.write(f"{total}\n")
