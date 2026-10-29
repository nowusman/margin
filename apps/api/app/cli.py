from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from margin_db.object_store_factory import ObjectStoreConfig, create_object_store
from margin_db.seed import seed_domains_and_vocabulary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="margin-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser(
        "seed-domains", help="Seed canonical domains, vocabulary, groups, and model catalog entries."
    )
    seed_parser.add_argument("--database-url", default=None)
    seed_parser.add_argument("--no-commit", action="store_true")
    subparsers.add_parser(
        "ensure-object-store-bucket", help="Create the configured S3-compatible object-store bucket if it is missing."
    )

    args = parser.parse_args(argv)

    if args.command == "seed-domains":
        settings = get_settings()
        engine = create_engine(args.database_url or settings.database_url)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as session:
            result = seed_domains_and_vocabulary(session, commit=not args.no_commit)
        print(
            "Seeded domains "
            f"(created={result.domains_created}, updated={result.domains_updated}) "
            "and vocabulary "
            f"(created={result.vocabulary_created}, updated={result.vocabulary_updated}) "
            "and groups "
            f"(created={result.groups_created}, updated={result.groups_updated}) "
            "and models "
            f"(created={result.models_created})."
        )
        return 0

    if args.command == "ensure-object-store-bucket":
        settings = get_settings()
        object_store = create_object_store(
            ObjectStoreConfig(
                endpoint=settings.object_store_endpoint,
                bucket=settings.object_store_bucket,
                region=settings.object_store_region,
                access_key_id=settings.object_store_access_key_id,
                secret_access_key=settings.object_store_secret_access_key,
                session_token=settings.object_store_session_token,
                filesystem_root=Path(settings.object_store_local_path),
            )
        )
        object_store.ensure_ready()
        print(f"Object store bucket ready: {settings.object_store_bucket}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
