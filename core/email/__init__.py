"""JAR email ingestion pipeline.

A simple IMAP → embed → LanceDB flow: fetch every message, embed subject+body
locally with bge-m3, and upsert into a local vector store for semantic search.
Everything runs on-device, so no redaction is needed. See pipeline.py for the
orchestrator and vector_store.py for the LanceDB layer.
"""
