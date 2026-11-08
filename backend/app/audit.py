"""Append-only application audit; called inside the mutation's transaction."""
from . import store

def record(conn, actor, action, entity_id=None, *, run_id=None, **details):
    conn.execute(store.audit_events.insert().values(created_at=store.now(), actor_id=actor,
        action=action, entity_id=entity_id, run_id=run_id, details=details))
