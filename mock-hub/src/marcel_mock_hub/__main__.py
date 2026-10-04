import uvicorn

from .app import create_app

uvicorn.run(create_app(), host='127.0.0.1', port=7499, log_level='info')
