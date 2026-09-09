"""Monitor administration. CLI and authenticated Gateway commands share operations."""
from .config import load_config, write_yaml
from .errors import KiokukoError
from .filesystem import file_lock
from .models import now
from .orca_transport import read_trace, runtime_check


def setup_parser(parser):
    commands = parser.add_subparsers(dest='monitor_action', required=True)
    for command in ('enable','disable','status','runs'):
        commands.add_parser(command)
    for command in ('show','retry','purge'):
        commands.add_parser(command).add_argument('run_id')


def execute(service, args):
    from .identity import bound_values
    bound = bound_values()
    if bound.get('PLATFORM') not in {None, '', 'cli'} or bound.get('USER_ID') or bound.get('CHAT_TYPE') in {'group','dm','private'}:
        raise KiokukoError('LOCAL_CLI_REQUIRED')
    return _execute(service, args)


def _execute(service, args):
    """Called only after the entry point has authorized the administrator."""
    from .monitor import status, remove_run
    action = args.monitor_action
    if action in {'enable','disable'}:
        if action == 'enable':
            runtime_check()
        with file_lock(service.store.directory/'config.lock', exclusive=True):
            cfg = load_config(service.store.home)
            cfg['monitor']['enabled'] = action == 'enable'
            write_yaml(service.store.directory/'config.yaml', cfg)
        if action == 'disable':
            from .monitor import release_home
            release_home(service.store.home)
        return {**status(service), 'additional_model_calls': 'all completed turns',
                'scope': 'principal/workspace for CLI and DM; conversation/workspace for groups',
                'memory_promotion': 'unverified experience only', 'experience_valid_days':90}
    if action == 'status':
        return status(service)
    if action == 'runs':
        with service.transaction() as db:
            return [dict(row) for row in db.execute('SELECT id,state,created_at,completed_at,bytes,dropped,error_code FROM monitor_runs ORDER BY created_at DESC')]
    with service.transaction() as db:
        row = db.execute('SELECT * FROM monitor_runs WHERE id=?', (args.run_id,)).fetchone()
        if not row:
            raise KiokukoError('MONITOR_RUN_NOT_FOUND')
        row = dict(row)
    if action == 'show':
        if row['state'] != 'complete':
            return {'run':row, 'events':[], 'complete':False}
        try:
            events = read_trace(service.store.directory,row)
            return {'run':row,'events':[{'seq':e.seq,'type':e.type,'attrs':dict(e.attrs),'payload':p} for e,p in events]}
        except KiokukoError as e:
            return {'run':row,'error':e.code,'complete':False}
    if action == 'purge':
        remove_run(service,args.run_id)
        return {'run_id':args.run_id,'purged':True,'scope':'Source trace and extraction job. Derived memories, Hermes history and backups are separate.'}
    if action == 'retry':
        read_trace(service.store.directory,row)
        if row['state'] != 'complete' or not service.config['monitor']['enabled']:
            raise KiokukoError('MONITOR_RETRY_DENIED')
        with service.transaction(write=True) as db:
            db.execute("UPDATE experience_jobs SET state='pending',error_code=NULL,updated_at=? WHERE run_id=? AND state='failed'", (now(),args.run_id))
        from .experiences import process_next
        process_next(service)
        return status(service)
    raise KiokukoError('INVALID_COMMAND')
