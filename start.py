import sys,venv,subprocess,platform,json,urllib.request,os
from pathlib import Path
base=Path(__file__).resolve().parent
python=base/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
if not python.exists():
 if sys.version_info<(3,11):raise SystemExit('Python 3.11 or newer is required.')
 venv.create(base/'.venv',with_pip=True)
try:subprocess.run([str(python),'-c','import fastapi,uvicorn,mcp'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
except subprocess.CalledProcessError:
 args=[str(python),'-m','pip','install','-r',str(base/'requirements.txt')]
 if (base/'wheels').exists() and sys.platform=='win32' and sys.version_info[:2]==(3,13):args+=['--no-index','--find-links',str(base/'wheels')]
 subprocess.run(args,check=True,cwd=base)
port=int(os.getenv('APP_PORT','8877'))
url=f'http://127.0.0.1:{port}'
try:
 with urllib.request.urlopen(url+'/api/health',timeout=2) as r:health=json.load(r)
 if health.get('backend')=='Python/FastAPI' and health.get('database')=='SQLite':
  print('Website already running:',url);raise SystemExit(0)
except (OSError,ValueError):pass
print('Open '+url+' ; press Ctrl+C to stop.',flush=True)
subprocess.run([str(python),str(base/'run.py')],cwd=base,check=True)
