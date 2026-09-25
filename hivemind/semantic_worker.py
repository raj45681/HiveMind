"""Isolated FastEmbed process. Setup may download a model; searches never do."""
import json
import sys


def main():
    request = json.load(sys.stdin)
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name="BAAI/bge-small-en-v1.5",
        cache_dir=request["cache"],
        local_files_only=not request.get("download", False),
        threads=2,
    )
    vectors = model.embed(request["texts"], batch_size=32)
    json.dump([vector.tolist() for vector in vectors], sys.stdout)


if __name__ == "__main__":
    main()
