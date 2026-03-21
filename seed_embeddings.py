"""Generate embeddings for all canonical URLs.

Usage:
    python seed_embeddings.py          # Uses TF-IDF (local, free)
    python seed_embeddings.py --openai # Uses OpenAI API (requires OPENAI_API_KEY)

Set USE_OPENAI_EMBEDDING=True in .env to default to OpenAI.
"""
import sys
import time

from app import create_app, db
from app.models import CanonicalUrl, UrlEmbedding
from app.services.embeddings import build_text_for_url, generate_embeddings_batch

app = create_app()


def seed_tfidf():
    """Generate TF-IDF embeddings locally — no API key needed."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    with app.app_context():
        all_urls = CanonicalUrl.query.all()
        print(f"Building text for {len(all_urls)} URLs...")

        texts = []
        url_ids = []
        for cu in all_urls:
            text = build_text_for_url(cu.id)
            if text:
                texts.append(text)
                url_ids.append(cu.id)

        print(f"Built {len(texts)} texts. Running TF-IDF...")
        vectorizer = TfidfVectorizer(max_features=512, stop_words="english", sublinear_tf=True)
        tfidf_matrix = vectorizer.fit_transform(texts)
        print(f"TF-IDF matrix: {tfidf_matrix.shape}")

        # Clear old embeddings
        UrlEmbedding.query.delete()
        db.session.commit()

        print("Storing embeddings...")
        for i, (url_id, text) in enumerate(zip(url_ids, texts)):
            vec = tfidf_matrix[i].toarray()[0].tolist()
            emb = UrlEmbedding(
                canonical_url_id=url_id,
                text_content=text[:2000],
                model="tfidf-512",
                dimensions=len(vec),
            )
            emb.set_vector(vec)
            db.session.add(emb)
            if (i + 1) % 1000 == 0:
                db.session.commit()
                print(f"  {i + 1}/{len(url_ids)} stored...")

        db.session.commit()
        print(f"Done. {UrlEmbedding.query.count()} TF-IDF embeddings stored.")


def seed_openai():
    """Generate OpenAI embeddings — requires OPENAI_API_KEY."""
    with app.app_context():
        api_key = app.config.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: No OPENAI_API_KEY found in .env")
            print("Add OPENAI_API_KEY=sk-... to your .env file")
            sys.exit(1)

        # Clear old embeddings and regenerate with OpenAI
        UrlEmbedding.query.delete()
        db.session.commit()

        all_urls = CanonicalUrl.query.all()
        to_embed = all_urls

        print(f"Total canonical URLs: {len(all_urls)}")
        print(f"To embed: {len(to_embed)}")

        if not to_embed:
            print("Nothing to do.")
            return

        batch_size = 100
        total_embedded = 0

        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i:i + batch_size]
            texts = []
            batch_ids = []

            for cu in batch:
                text = build_text_for_url(cu.id)
                if text:
                    texts.append(text)
                    batch_ids.append(cu.id)

            if not texts:
                continue

            print(f"  Batch {i // batch_size + 1}: embedding {len(texts)} URLs...", end=" ", flush=True)

            try:
                vectors = generate_embeddings_batch(texts, api_key)
            except Exception as e:
                print(f"ERROR: {e}")
                time.sleep(5)
                continue

            if not vectors:
                print("FAILED (API returned no vectors)")
                time.sleep(2)
                continue

            for canonical_url_id, text, vector in zip(batch_ids, texts, vectors):
                emb = UrlEmbedding(
                    canonical_url_id=canonical_url_id,
                    text_content=text[:2000],
                    dimensions=len(vector),
                )
                emb.set_vector(vector)
                db.session.add(emb)

            db.session.commit()
            total_embedded += len(vectors)
            print(f"OK ({total_embedded}/{len(to_embed)})")

            # Rate limit: ~3 batches/min for safety
            if i + batch_size < len(to_embed):
                time.sleep(1)

        print(f"\nDone. Embedded {total_embedded} URLs total.")
        print(f"Total embeddings in DB: {UrlEmbedding.query.count()}")


if __name__ == "__main__":
    use_openai = "--openai" in sys.argv
    if not use_openai:
        with app.app_context():
            use_openai = app.config.get("USE_OPENAI_EMBEDDING", False)

    if use_openai:
        print("Using OpenAI embeddings (text-embedding-3-small)")
        seed_openai()
    else:
        print("Using TF-IDF embeddings (local, no API key needed)")
        seed_tfidf()
