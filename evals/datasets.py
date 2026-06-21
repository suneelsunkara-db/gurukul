"""Build evaluation datasets from Gurukul's Lakebase data.

Each builder returns a list of dicts ready for mlflow.genai.evaluate().
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _fetch_topics():
    from agent_server.db import GurukuDB, SCHEMA
    from psycopg.rows import dict_row

    db = GurukuDB()
    pool = await db._pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT id, title, category, payload FROM {SCHEMA}.topics "
                "WHERE status = 'done' AND payload IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 50"
            )
            return await cur.fetchall()


async def _fetch_explorations():
    from agent_server.db import GurukuDB, SCHEMA
    from psycopg.rows import dict_row

    db = GurukuDB()
    pool = await db._pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT seed FROM {SCHEMA}.explorations "
                "ORDER BY created_at DESC LIMIT 20"
            )
            rows = await cur.fetchall()

            await cur.execute(
                f"SELECT id, title, category FROM {SCHEMA}.topics "
                "WHERE status = 'done' ORDER BY created_at DESC LIMIT 50"
            )
            topics = await cur.fetchall()

            await cur.execute(
                f"SELECT source_id, target_id, edge_type, label, strength "
                f"FROM {SCHEMA}.graph_edges"
            )
            edges = await cur.fetchall()

            return rows, topics, edges


async def _fetch_challenge_sessions():
    from agent_server.db import GurukuDB, SCHEMA
    from psycopg.rows import dict_row

    db = GurukuDB()
    pool = await db._pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT cs.*, t.title, t.payload "
                f"FROM {SCHEMA}.challenge_sessions cs "
                f"JOIN {SCHEMA}.topics t ON cs.topic_id = t.id "
                "WHERE cs.completed_at IS NOT NULL "
                "ORDER BY cs.completed_at DESC LIMIT 30"
            )
            return await cur.fetchall()


def build_student_eval_dataset():
    """Build eval dataset from generated topic content.

    Each row has:
      - inputs: {"topic_id": ..., "title": ..., "category": ...}
      - outputs: the parsed payload (Student output)
    """
    try:
        topics = _run_async(_fetch_topics())
    except Exception as e:
        logger.warning("Could not fetch topics from Lakebase: %s", e)
        return []

    dataset = []
    for t in topics:
        payload = t.get("payload")
        if not payload:
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue

        dataset.append({
            "inputs": {
                "topic_id": t["id"],
                "title": t["title"],
                "category": t["category"],
            },
            "outputs": payload,
        })

    logger.info(f"Built student eval dataset with {len(dataset)} rows")
    return dataset


def build_teacher_eval_dataset():
    """Build eval dataset from decomposition results.

    Each row has:
      - inputs: {"seed": ...}
      - outputs: {"topics": [...], "edges": [...]}
    """
    try:
        rows, topics, edges = _run_async(_fetch_explorations())
    except Exception as e:
        logger.warning("Could not fetch explorations from Lakebase: %s", e)
        return []

    if not rows:
        return []

    topics_by_seed = {}
    for t in topics:
        seed = t.get("seed", "")
        if seed not in topics_by_seed:
            topics_by_seed[seed] = []
        topics_by_seed[seed].append(t)

    dataset = []
    seen_seeds = set()
    for r in rows:
        seed = r["seed"]
        if seed in seen_seeds:
            continue
        seen_seeds.add(seed)

        seed_topics = topics_by_seed.get(seed, [])
        if not seed_topics:
            continue

        topic_ids = {t["id"] for t in seed_topics}
        seed_edges = [
            e for e in edges
            if e["source_id"] in topic_ids or e["target_id"] in topic_ids
        ]

        dataset.append({
            "inputs": {"seed": seed},
            "outputs": {
                "topics": seed_topics,
                "edges": seed_edges,
            },
        })

    logger.info(f"Built teacher eval dataset with {len(dataset)} rows")
    return dataset


def build_examiner_eval_dataset():
    """Build eval dataset from completed challenge sessions.

    Each row has:
      - inputs: {"topic_id": ..., "topic_title": ..., "topic_content": ...}
      - outputs: the evaluation from each round
    """
    try:
        sessions = _run_async(_fetch_challenge_sessions())
    except Exception as e:
        logger.warning("Could not fetch challenge sessions from Lakebase: %s", e)
        return []

    dataset = []
    for s in sessions:
        rounds = s.get("rounds", [])
        if isinstance(rounds, str):
            try:
                rounds = json.loads(rounds)
            except json.JSONDecodeError:
                continue

        for r in rounds:
            evaluation = r.get("evaluation")
            if not evaluation:
                continue

            dataset.append({
                "inputs": {
                    "topic_id": s["topic_id"],
                    "topic_title": s.get("title", ""),
                    "question": r.get("question", ""),
                    "answer": r.get("answer", ""),
                },
                "outputs": evaluation,
            })

    logger.info(f"Built examiner eval dataset with {len(dataset)} rows")
    return dataset
