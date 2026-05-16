import numpy as np
from sentence_transformers import SentenceTransformer
import structlog

log = structlog.get_logger()

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Process-level cache. The MiniLM model is ~500 MB; instantiating one
# per Pipeline.run() means 4-way parallel sweeps allocate 2 GB just for
# embedders. Cache by model name so concurrent Deduplicator instances
# in the same process share one underlying model.
_MODEL_CACHE: dict[str, SentenceTransformer] = {}


class ArticleEmbedder:
    def __init__(self, model_name: str = MODEL_NAME):
        if model_name not in _MODEL_CACHE:
            log.info("loading_embedding_model", model=model_name)
            _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
        self.model = _MODEL_CACHE[model_name]

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Batch encode texts. Returns float32 array of shape (n, embedding_dim)."""
        if not texts:
            return np.array([])
        embeddings = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=len(texts) > 10,
            convert_to_numpy=True,
            normalize_embeddings=True,  # L2 normalize so cosine = dot product
        )
        return embeddings.astype(np.float32)

    def cosine_similarity_matrix(self, embeddings: np.ndarray) -> np.ndarray:
        """
        Compute full pairwise cosine similarity matrix.
        With L2-normalized embeddings this is equivalent to the dot product matrix.
        Returns an (n, n) float32 array with values in [-1, 1].
        """
        return np.dot(embeddings, embeddings.T)

    def cosine_similarity(self, emb_a: np.ndarray, emb_b: np.ndarray) -> float:
        """
        Single pairwise cosine similarity between two L2-normalized embedding vectors.
        With normalized embeddings, dot product == cosine similarity.
        """
        return float(np.dot(emb_a, emb_b))
