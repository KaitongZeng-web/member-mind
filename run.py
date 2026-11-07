import os,uvicorn
if __name__=='__main__':uvicorn.run('backend.app:app',host=os.getenv('APP_HOST','127.0.0.1'),port=int(os.getenv('APP_PORT','8877')),reload=False)
