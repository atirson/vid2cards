"""Vid2Cards command-line interface."""

from __future__ import annotations

import argparse
import sys
import time

from . import pipeline, playlist
from .config import load_config
from .db import Database, run_lock
from .generate import generate_material
from .logconfig import configure_logging


def cmd_run(args):
    config = load_config()
    logger = configure_logging(config.logs_dir)
    playlist.update_yt_dlp()
    db = Database(config.db_path)
    try:
        with run_lock(config.lock_path):
            path = pipeline.run(config, db)
            if path:
                print(f"Package generated: {path}")
            else:
                print("No new videos processed today.")
    finally:
        db.close()


def cmd_sync(args):
    config = load_config()
    configure_logging(config.logs_dir)
    db = Database(config.db_path)
    try:
        with run_lock(config.lock_path):
            pipeline.sync(config, db)
    finally:
        db.close()


def cmd_add(args):
    config = load_config()
    configure_logging(config.logs_dir)
    db = Database(config.db_path)
    try:
        video_id = playlist.extract_id(args.url)
        meta = playlist.get_metadata(video_id, config.cookies_file)
        db.register_new(meta.video_id, meta.title, meta.duration_s, "", "Avulsos")
        print(f"Added: {meta.title} ({meta.video_id})")
    finally:
        db.close()


def cmd_status(args):
    config = load_config()
    db = Database(config.db_path)
    try:
        rows = db.all_videos()
        if not rows:
            print("No videos registered yet.")
            return
        title_width = max(10, min(50, max(len(r["title"] or "") for r in rows)))
        print(f"{'video_id':12} {'state':16} {'attempts':10} {'title':<{title_width}}")
        for r in rows:
            title = (r["title"] or "")[:title_width]
            print(f"{r['video_id']:12} {r['state']:16} {r['attempts']:<10} {title}")
    finally:
        db.close()


def cmd_retry(args):
    config = load_config()
    configure_logging(config.logs_dir)
    db = Database(config.db_path)
    try:
        db.retry_errors(args.id)
        print("OK: video(s) in error are back in the queue." if args.id else "OK: all errors are back in the queue.")
    finally:
        db.close()


def cmd_regen(args):
    config = load_config()
    configure_logging(config.logs_dir)
    db = Database(config.db_path)
    try:
        cards_path = pipeline.cards_path_for(config, args.id)
        cards_path.unlink(missing_ok=True)
        db.update_state(args.id, "transcribed")
        pipeline.generation_phase(config, db, [args.id])
    finally:
        db.close()


def cmd_export(args):
    config = load_config()
    configure_logging(config.logs_dir)
    db = Database(config.db_path)
    try:
        rows = db.conn.execute(
            "SELECT video_id FROM videos WHERE state IN ('cards_generated', 'exported')"
        ).fetchall()
        video_ids = [r["video_id"] for r in rows]
        # force re-export even of already 'exported' ones, without losing the rest of the state
        for vid in video_ids:
            db.conn.execute("UPDATE videos SET state = 'cards_generated' WHERE video_id = ?", (vid,))
        db.conn.commit()
        path = pipeline.export_phase(config, db, video_ids)
        print(f"Exported: {path}" if path else "Nothing to export.")
    finally:
        db.close()


def cmd_bench(args):
    config = load_config()
    test_text = args.test_file and open(args.test_file, encoding="utf-8").read()
    if not test_text:
        sys.exit("Use --test-file <path> with a sample transcript.")

    models = [config.llm_model, config.llm_alternative_model]
    for model in models:
        print(f"\n=== {model} ===")
        t0 = time.time()
        material = generate_material(
            test_text, "Benchmark test", model,
            num_ctx=config.llm_num_ctx, temperature=config.llm_temperature,
        )
        print(f"Time: {time.time() - t0:.1f}s | cards: {len(material.cards)} | quiz: {len(material.quiz)}")


def main():
    ap = argparse.ArgumentParser(prog="vid2cards")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("sync").set_defaults(func=cmd_sync)

    p_add = sub.add_parser("add")
    p_add.add_argument("url")
    p_add.set_defaults(func=cmd_add)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p_retry = sub.add_parser("retry")
    p_retry.add_argument("--id", default=None)
    p_retry.set_defaults(func=cmd_retry)

    p_regen = sub.add_parser("regen")
    p_regen.add_argument("--id", required=True)
    p_regen.set_defaults(func=cmd_regen)

    p_export = sub.add_parser("export")
    p_export.add_argument("--all", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_bench = sub.add_parser("bench")
    p_bench.add_argument("--test-file", default=None)
    p_bench.set_defaults(func=cmd_bench)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
