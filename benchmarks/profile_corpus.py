"""Synthetic-only stores for repeatable lexical and profile probe measurements."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from hermes_kiokuko.models import Identity, canonical, digest, now
from hermes_kiokuko.service import insert
from hermes_kiokuko.retrieval import tokens
from hermes_kiokuko.task_profiles import project
from hermes_kiokuko.profile_resolver import POLICY_VERSION
from hermes_kiokuko.workspace import resolve_workspace


def populate(service, root, entries, profiles):
    root = root.resolve()
    (root/'src').mkdir(parents=True)
    (root/'src/needle.py').write_text('pass\n')
    (root/'src/other.py').write_text('pass\n')
    who=Identity('cli','cli','profile-owner','synthetic',resolve_workspace(root),'dm')
    snap=service.snapshot('synthetic','seed','synthetic seed',who,workspace_root=root)
    stamp=now()
    with service.transaction(write=True) as db:
        template=service._create(db,snap,'synthetic seed','principal_workspace')
        for i in range(entries):
            row={**template,'id':f'mem_bench_{i:08d}','claim':f'needle setting {i}' if i%100==0 else f'other setting {i}',
                 'principal_id':who.principal_id if i%4==0 else None,
                 'scope_type':'principal_workspace' if i%4==0 else 'conversation_workspace',
                 'conversation_id':None if i%4==0 else 'unrelated'}
            # Use existing authenticated conversation identity in a different scope.
            if i%4:
                db.execute("INSERT OR IGNORE INTO conversations(id,platform,chat_type,conversation_hmac,created_at,updated_at) VALUES ('unrelated','cli','dm','unrelated',?,?)",(stamp,stamp))
            row['normalized_claim']=row['claim']
            row['content_sha256']=digest(row['claim'])
            insert(db,'memory_entries',row)
            # Brand-new synthetic IDs need no DELETE-before-reindex work. Keep
            # the real revision/document/FTS/gram format without quadratic setup.
            insert(db,'memory_revisions',{'entry_id':row['id'],'revision':1,'operation':'create',
                   'snapshot_json':canonical(row),'actor':'synthetic','created_at':stamp})
            insert(db,'memory_search_documents',{'entry_id':row['id'],'entry_revision':1,
                   'subject_key':row['subject_key'],'claim':row['claim']})
            db.executemany('INSERT INTO memory_ngrams VALUES (?,?,?)',
                           [(token,row['id'],1) for token in tokens(row['claim'])])
        for i in range(profiles):
            target='src/needle.py' if i==0 else 'src/other.py'
            excerpt=f'Inspect `{target}`'
            source={**asdict(snap),'turn_id':f'p{i}','user_content_sha256':digest(excerpt)}
            insert(db,'turn_snapshots',{**source,'created_at':stamp})
            db.execute('INSERT INTO snapshot_roots VALUES (?,?,?,?,?)',(*snap.key[:2],source['turn_id'],str(root),who.workspace_id))
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key[:2],source['turn_id'],stamp))
            row={'id':f'profile_bench_{i:08d}','profile_key':snap.profile_key,'session_id':snap.session_id,
                 'turn_id':source['turn_id'],'session_generation':1,'principal_id':who.principal_id,
                 'conversation_id':who.conversation_id,'workspace_id':who.workspace_id,'excerpt':excerpt,
                 'targets_json':canonical([target]),'content_sha256':digest(canonical([excerpt,[target]])),
                 'policy_version':POLICY_VERSION,'created_at':stamp,
                 'expires_at':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(timespec='microseconds')}
            insert(db,'task_profiles',row)
            project(db,row)
    return who
