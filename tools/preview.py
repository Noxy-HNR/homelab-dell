"""Create isolated example data for local UI checks. No machine settings change."""
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'preview-data'
TOKEN='local-preview-only-never-use-this-token-on-a-server'
for role,port in [('hp',18088),('dell',18090)]:
    home=BASE/role
    for name in ('state','models','projects','config'):
        (home/name).mkdir(parents=True,exist_ok=True)
    data=dict(role=role,listen_host='127.0.0.1',port=port,
              state_dir=str(home/'state'),models_dir=str(home/'models'),
              projects_dir=str(home/'projects'),config_dir=str(home/'config'),
              hp_url='http://127.0.0.1:18088',dell_url='http://127.0.0.1:18090',
              token=TOKEN,allow_network_downloads=False)
    (BASE/f'{role}.json').write_text(json.dumps(data,indent=2))
def text(value):
    raw=value.encode();return struct.pack('<Q',len(raw))+raw
head=b'GGUF'+struct.pack('<IQQ',3,1,1)+text('general.architecture')+struct.pack('<I',8)+text('test-fixture')
head+=text('example.weight')+struct.pack('<IQIQ',1,1,0,0)
head+=b'\0'*((-len(head))%32)+struct.pack('<f',1)
(BASE/'hp/models/Example-only-not-a-real-model.gguf').write_bytes(head)
print('Isolated preview data ready. Start homelab.app with preview-data/hp.json or dell.json.')
