"""Ingest 10,000 distinct documents through a pipeline with a docstore.

With a docstore and no vector store the pipeline deduplicates by hash
in _handle_duplicates, whose within-batch check the reference change
turns from a list into a set. The one transformation passes nodes
through unchanged, so no tokenizer or model is loaded and nothing is
fetched. Prints the number of nodes kept so both trees can be compared
for equal output.
"""

import sys
from collections.abc import Sequence
from typing import Any

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.schema import BaseNode, TransformComponent
from llama_index.core.storage.docstore import SimpleDocumentStore


class PassThrough(TransformComponent):
    """Return the nodes unchanged."""

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> Any:
        """Return the nodes unchanged."""
        return list(nodes)


docs = [
    Document(text=f"document number {i}", id_=f"d{i}") for i in range(10000)
]
pipeline = IngestionPipeline(
    transformations=[PassThrough()], docstore=SimpleDocumentStore()
)
nodes = pipeline.run(documents=docs)
sys.stdout.write(f"{len(nodes)}\n")
