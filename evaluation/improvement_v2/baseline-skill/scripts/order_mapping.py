#!/usr/bin/env python3
"""Portable local CLI; all business logic lives in order_mapping_core."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='聊天订单映射、目录版本管理和Excel导出（本地运行）')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['catalog-register', 'catalog-current', 'catalog-withdraw']:
        sub = commands.add_parser(name)
        sub.add_argument('--runtime', required=True)
        if name == 'catalog-register':
            sub.add_argument('--file', required=True)
            sub.add_argument('--reference', action='store_true')
        if name == 'catalog-withdraw':
            sub.add_argument('--reason', required=True)
    sub = commands.add_parser('parse')
    sub.add_argument('--chat', required=True)
    sub.add_argument('--output')
    sub = commands.add_parser('validate-config')
    sub.add_argument('--config', required=True)
    sub = commands.add_parser('run')
    sub.add_argument('--runtime', required=True)
    sub.add_argument('--batch', required=True)
    sub.add_argument('--output', required=True)
    sub = commands.add_parser('merge-finance')
    sub.add_argument('--runtime', required=True)
    sub.add_argument('--manifests', nargs='+', required=True)
    sub.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        from order_mapping_core.catalog import CatalogStore
        from order_mapping_core.parsing import parse_chat
        from order_mapping_core.workflow import process_batch, validate_config, merge_finance
        if args.command.startswith('catalog-'):
            store = CatalogStore(args.runtime)
            if args.command == 'catalog-register':
                result = store.register(args.file, reference=args.reference)
            elif args.command == 'catalog-withdraw':
                result = store.withdraw_pending(args.reason)
            else:
                result = store.current()
            records = result.pop('records', None)
            if records is not None:
                result['record_count'] = len(records)
        elif args.command == 'parse':
            path = Path(args.chat)
            result = parse_chat(path.read_text(encoding='utf-8-sig'), path.name)
            if args.output:
                from order_mapping_core.workflow import _write_json
                _write_json(args.output, result)
                result = {'item_count': len(result), 'output': str(Path(args.output).resolve())}
        elif args.command == 'validate-config':
            result = validate_config(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
        elif args.command == 'merge-finance':
            result = merge_finance(args.runtime, args.manifests, args.output)
        else:
            path = Path(args.batch).resolve()
            result = process_batch(args.runtime, json.loads(path.read_text(encoding='utf-8-sig')), args.output, base_dir=path.parent)
            result = {k: v for k, v in result.items() if k != 'order'}
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, TimeoutError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'type': type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
