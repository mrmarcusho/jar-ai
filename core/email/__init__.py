"""JAR email ingestion pipeline.

Six-stage IMAP → triage → extract → store pipeline that turns a mailbox into
financial events (transactions) and prose documents, reusing JAR's existing
PII redaction and PDF extraction engines. See pipeline.py for the orchestrator.
"""
