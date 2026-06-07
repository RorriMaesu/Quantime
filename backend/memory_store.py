# backend/memory_store.py
import os
import time
import urllib.request
import json
import logging
from typing import List, Dict, Any, Optional

# Setup logger
logger = logging.getLogger("quantime.memory_store")
logging.basicConfig(level=logging.INFO)

# Check if chromadb is installed
try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("chromadb library not installed. Falling back to SQLite-based semantic memory mock.")

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
FALLBACK_EMBED_MODEL = "gemma4:12b"

def get_ollama_embedding(text: str) -> List[float]:
    """
    Computes text embeddings using the local Ollama instance.
    Falls back to a hashing vectorizer if Ollama is unreachable or errors.
    """
    payload = {
        "model": DEFAULT_EMBED_MODEL,
        "prompt": text
    }
    
    # Try nomic-embed-text first
    for model in [DEFAULT_EMBED_MODEL, FALLBACK_EMBED_MODEL]:
        payload["model"] = model
        try:
            req = urllib.request.Request(
                OLLAMA_EMBED_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            # Timeout of 3 seconds to avoid blocking
            with urllib.request.urlopen(req, timeout=3.0) as response:
                res = json.loads(response.read().decode("utf-8"))
                if "embedding" in res:
                    return res["embedding"]
        except Exception as e:
            logger.debug(f"Ollama embedding with model {model} failed: {e}")
            
    # Hashing fallback: Create a pseudo-embedding vector of 128 dimensions
    # so the app doesn't crash if Ollama isn't configured for embeddings.
    logger.debug("Ollama embedding failed or timed out. Generating deterministic fallback hash vector.")
    import hashlib
    dim = 128
    vector = [0.0] * dim
    # Hash sections of the text to seed float values
    for i in range(dim):
        seed = f"{text}_{i}".encode("utf-8")
        h = hashlib.sha256(seed).hexdigest()
        val = int(h[:8], 16) / 4294967295.0  # Normalize to [0, 1]
        vector[i] = val
    return vector

class SQLiteMemoryFallback:
    """
    Fallback semantic store when ChromaDB is not installed.
    Uses basic cosine similarity over hashed tokens stored in a local SQLite file.
    """
    def __init__(self, db_path: str = "quantime.db"):
        self.db_path = db_path
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        # Create a table for chat memories
        conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_memories (
            id TEXT PRIMARY KEY,
            document TEXT NOT NULL,
            metadata TEXT NOT NULL,
            vector TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
        """)
        conn.commit()
        conn.close()

    def add(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        for idx, doc_id in enumerate(ids):
            meta_str = json.dumps(metadatas[idx])
            vec_str = json.dumps(embeddings[idx])
            conn.execute(
                "INSERT OR REPLACE INTO semantic_memories (id, document, metadata, vector, timestamp) VALUES (?, ?, ?, ?, ?)",
                (doc_id, documents[idx], meta_str, vec_str, now)
            )
        conn.commit()
        conn.close()

    def query(self, query_embeddings: List[List[float]], n_results: int = 3) -> Dict[str, Any]:
        """
        Calculates cosine similarity in Python over stored items.
        """
        import sqlite3
        import math
        
        q_vec = query_embeddings[0]
        q_norm = math.sqrt(sum(v * v for v in q_vec))
        if q_norm == 0:
            q_norm = 1.0
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, document, metadata, vector FROM semantic_memories")
        rows = cursor.fetchall()
        conn.close()
        
        scored_results = []
        for doc_id, doc, meta_str, vec_str in rows:
            try:
                vec = json.loads(vec_str)
                # Compute dot product
                dot = sum(a * b for a, b in zip(q_vec, vec))
                v_norm = math.sqrt(sum(v * v for v in vec))
                if v_norm == 0:
                    v_norm = 1.0
                similarity = dot / (q_norm * v_norm)
                scored_results.append({
                    "id": doc_id,
                    "document": doc,
                    "metadata": json.loads(meta_str),
                    "score": similarity
                })
            except Exception:
                continue
                
        # Sort by similarity score descending
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        top_k = scored_results[:n_results]
        
        return {
            "ids": [[item["id"] for item in top_k]],
            "documents": [[item["document"] for item in top_k]],
            "metadatas": [[item["metadata"] for item in top_k]],
            "distances": [[1.0 - item["score"] for item in top_k]] # Distance is 1 - similarity
        }

class SemanticMemoryStore:
    """
    Wrapper interface that exposes add and query functions for ChromaDB,
    delegating to SQLite fallback if ChromaDB is unavailable.
    """
    def __init__(self, persist_dir: str = "chroma_db"):
        self.persist_dir = persist_dir
        self.chroma_client = None
        self.collection = None
        self.fallback_db = None
        
        if CHROMA_AVAILABLE:
            try:
                self.chroma_client = chromadb.PersistentClient(path=self.persist_dir)
                self.collection = self.chroma_client.get_or_create_collection(
                    name="quantime_memories",
                    metadata={"hnsw:space": "cosine"}
                )
                logger.info("ChromaDB persistent client loaded.")
            except Exception as e:
                logger.error(f"Failed to initialize ChromaDB client: {e}. Falling back to SQLite memory.")
                self.collection = None
                
        if self.collection is None:
            self.fallback_db = SQLiteMemoryFallback()
            logger.info("SQLite semantic memory fallback active.")

    def add_interaction(self, doc_id: str, text_content: str, metadata: Dict[str, Any]) -> None:
        """
        Embeds text content and stores it in semantic memory.
        """
        embedding = get_ollama_embedding(text_content)
        
        if self.collection is not None:
            try:
                self.collection.add(
                    ids=[doc_id],
                    documents=[text_content],
                    metadatas=[metadata],
                    embeddings=[embedding]
                )
                logger.info(f"Interaction {doc_id} stored in ChromaDB.")
                return
            except Exception as e:
                logger.error(f"ChromaDB write failed: {e}. Attempting SQLite backup.")
                
        # SQLite fallback fallback
        if self.fallback_db is None:
            self.fallback_db = SQLiteMemoryFallback()
        self.fallback_db.add(
            ids=[doc_id],
            documents=[text_content],
            metadatas=[metadata],
            embeddings=[embedding]
        )
        logger.info(f"Interaction {doc_id} stored in SQLite fallback.")

    def search_similar(self, query_text: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Searches semantic memories that are similar to the query.
        Returns a list of matching records.
        """
        embedding = get_ollama_embedding(query_text)
        
        if self.collection is not None:
            try:
                results = self.collection.query(
                    query_embeddings=[embedding],
                    n_results=limit
                )
                return self._parse_chroma_results(results)
            except Exception as e:
                logger.error(f"ChromaDB query failed: {e}. Falling back to SQLite memory search.")
                
        if self.fallback_db is None:
            self.fallback_db = SQLiteMemoryFallback()
        results = self.fallback_db.query([embedding], n_results=limit)
        return self._parse_chroma_results(results)

    def _parse_chroma_results(self, results: Dict[str, Any]) -> List[Dict[str, Any]]:
        parsed = []
        if not results or "ids" not in results or not results["ids"]:
            return parsed
            
        ids = results["ids"][0]
        documents = results["documents"][0] if "documents" in results else []
        metadatas = results["metadatas"][0] if "metadatas" in results else []
        distances = results["distances"][0] if "distances" in results else []
        
        for idx in range(len(ids)):
            parsed.append({
                "id": ids[idx],
                "content": documents[idx] if idx < len(documents) else "",
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "distance": distances[idx] if idx < len(distances) else 1.0
            })
        return parsed

# Global instance
memory_store = SemanticMemoryStore()

if __name__ == "__main__":
    # Test execution
    test_id = f"test_{int(time.time())}"
    memory_store.add_interaction(
        doc_id=test_id,
        text_content="Andrew Green is study chemistry on Monday afternoon at 2 PM.",
        metadata={"category": "schedule", "user": "Andrew"}
    )
    
    similar = memory_store.search_similar("Who is studying chemistry?")
    print("Search query response:")
    for doc in similar:
        print(f"- Content: {doc['content']} (Distance: {doc['distance']:.4f})")
