"""Submit a configured backup through the running app, and wait for its result."""
import argparse
import json
import time
from pathlib import Path

import httpx
from .config import load_settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='/etc/homelab/apps.json')
    parser.add_argument('--plan',required=True)
    parser.add_argument('--action',choices=['run','check'],default='run')
    args=parser.parse_args()
    s=load_settings(args.config)
    url=(s.hp_url if s.role=='hp' else s.dell_url).rstrip('/')
    with httpx.Client(base_url=url,headers={'Authorization':'Bearer '+s.token},timeout=30,trust_env=False) as client:
        response=client.post('/api/backups/'+args.action,json={'plan_id':args.plan})
        if response.status_code!=202:
            raise SystemExit(f'Could not queue the backup (HTTP {response.status_code}). Check the plan and app log.')
        task=response.json()
        print(f"Queued {args.plan} {args.action}: {task['id']}",flush=True)
        deadline=time.monotonic()+86500
        while task['state'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(5)
            response=client.get('/api/backups/tasks/'+task['id'])
            response.raise_for_status()
            task=response.json()
        print(task['state'],flush=True)
        if task['state']!='succeeded':
            raise SystemExit('Backup did not succeed. Open its task log in the web app.')


if __name__=='__main__':
    main()
