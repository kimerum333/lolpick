"""Operator-only journal status, JSONL export and DB replay (not an HTTP API)."""
import argparse
import json
from app.audit_journal import get_journal
from app.core.config import get_settings
from app.telemetry import persist_http_event
from app.db.session import dispose_audit_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "export", "replay", 'archive', 'replay-file'])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument('--file',help='Exact audit-*.jsonl filename within LOG_DIRECTORY/archive')
    parser.add_argument('--offset',type=int,default=0)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("Use --limit between 1 and 1000.")
    try:
        journal = get_journal()
        if args.command == "export":
            for line in journal.export():
                print(line)
        elif args.command == "status":
            print(json.dumps({**journal.summary(),**journal.storage_status()}))
        elif args.command == 'archive':
            print(json.dumps({'archived':journal.archive_pending(args.limit),**journal.storage_status()}))
        else:
            if not get_settings().system_log_enabled:
                parser.error("Enable SYSTEM_LOG_ENABLED before replaying DB copies.")
            if args.command == 'replay-file':
                if not args.file or args.offset < 0:
                    parser.error('Use --file and --offset >= 0 for file recovery.')
                print(json.dumps(journal.replay_file(args.file,persist_http_event,offset=args.offset,limit=args.limit)))
            else:
                print(json.dumps({"delivered": journal.replay(persist_http_event, args.limit), **journal.summary()}))
    finally:
        dispose_audit_engine()


if __name__ == "__main__":
    main()
