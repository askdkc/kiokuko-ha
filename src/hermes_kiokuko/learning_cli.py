"""Explicit local administrator controls; no model-tool mutation surface."""
from .config import load_config, write_yaml
from .errors import KiokukoError
from .filesystem import file_lock
from .identity import bound_values
from .models import now


def execute(service, action):
    bound = bound_values()
    if bound.get('PLATFORM') not in {None,'','cli'} or bound.get('USER_ID') or bound.get('CHAT_TYPE') in {'group','dm','private'}:
        raise KiokukoError('LOCAL_CLI_REQUIRED')
    from .learning import status, schedule_existing, refresh
    if action not in {'off','shadow','auto','status','retry'}:
        raise KiokukoError('INVALID_COMMAND')
    if action in {'off','shadow','auto'}:
        with file_lock(service.store.directory/'config.lock',exclusive=True):
            cfg = load_config(service.store.home)
            cfg['experience_learning']['mode'] = action
            write_yaml(service.store.directory/'config.yaml',cfg)
        with service.transaction(write=True) as db:
            # A mode change cancels in-flight commits, including off -> auto.
            db.execute("UPDATE learning_jobs SET state='pending',lease_token=NULL,input_hash=NULL,updated_at=? WHERE state='running'",(now(),))
            refresh(service,db)
            schedule_existing(service,db)
    if action == 'retry':
        with service.transaction(write=True) as db:
            db.execute("UPDATE learning_jobs SET state='pending',lease_token=NULL,error_code=NULL,updated_at=? WHERE state='failed'",(now(),))
    with service.transaction() as db:
        return {**status(db,service.config),'monitor_enabled':service.config['monitor']['enabled'],
                'automatic_use':'requires 3 distinct sessions and compatible observed conditions',
                'model_quality':'requires separate real-model evaluation; fixture tests are not certification'}
