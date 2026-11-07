import argparse
from pathlib import Path
from backend.store import Store
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('destination',help='New backup file path');args=p.parse_args();target=Path(args.destination).resolve()
 if target.exists():raise SystemExit('Backup target already exists; choose another name.')
 Store(Path(__file__).resolve().parent/'data/workspace.sqlite3').backup(target)
 print('SQLite backup complete:',target)
