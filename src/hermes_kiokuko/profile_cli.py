"""Local administrator operations for task-profile memory."""
from .config import load_config, write_yaml
from .errors import KiokukoError
from .filesystem import file_lock
from .identity import bound_values
from .task_profiles import MAX_PROFILES, RETENTION_DAYS, cleanup, delete, reindex, review


def setup_parser(parser):
    sub = parser.add_subparsers(dest='profile_action', required=True)
    for action in ('status', 'list', 'reindex'):
        sub.add_parser(action)
    sub.add_parser('delete').add_argument('id')
    mode = sub.add_parser('mode')
    mode.add_argument('mode', choices=('off', 'shadow', 'suggest', 'resolve'))


def execute(service, args, *, input_fn=input, output=print):
    bound = bound_values()
    from agent.delegation_context import is_delegated_child_context
    from tools.skill_provenance import get_current_write_origin
    if (bound.get('PLATFORM') not in {None, '', 'cli'} or bound.get('USER_ID') or bound.get('USER_ID_ALT')
            or bound.get('CHAT_TYPE') in {'group', 'dm', 'private'} or bound.get('CRON') == '1'
            or is_delegated_child_context() or get_current_write_origin() == 'background_review'):
        raise KiokukoError('LOCAL_CLI_REQUIRED')
    if args.profile_action == 'mode':
        with file_lock(service.store.directory / 'config.lock', exclusive=True):
            config = load_config(service.store.home)
            config['task_profile_memory']['mode'] = args.mode
            write_yaml(service.store.directory / 'config.yaml', config)
    if args.profile_action == 'delete':
        from .cli import confirm
        record, review_hash = review(service, args.id)
        confirm({'profile': record, 'effect': 'Delete live profile content and indexes. Prior hints become invalid. Hermes history and backups remain.'},
                args.id, input_fn=input_fn, output=output)
        return delete(service, args.id, review_hash)
    if args.profile_action == 'reindex':
        output('Rebuilding task-profile indexes; normal memory remains available.')
        return reindex(service)
    with service.transaction(write=True) as db:
        cleanup(db)
        if args.profile_action == 'list':
            return [dict(row) for row in db.execute('SELECT id,excerpt,targets_json,created_at,expires_at FROM task_profiles ORDER BY created_at DESC,id LIMIT 1000')]
        return {'mode': service.config['task_profile_memory']['mode'],
                'profiles': db.execute('SELECT count(*) FROM task_profiles').fetchone()[0],
                'retention_days': RETENTION_DAYS, 'max_profiles': MAX_PROFILES,
                'projection': db.execute("SELECT value FROM store_metadata WHERE key='task_profile_projection_version'").fetchone()[0],
                'resolutions': [dict(row) for row in db.execute('SELECT mode,status,reason,count(*) AS count FROM task_profile_resolutions GROUP BY mode,status,reason')],
                'source': 'past user mentions, not permission or verified success'}
